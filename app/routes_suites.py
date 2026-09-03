"""Eval-suite routes: list suites, kick off background runs, poll run status."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from . import runtime
from .arena import run_battle_headless, select_models
from .judge import JudgeError, run_judge
from .metrics import record_suite_run_completed, record_suite_run_started

log = logging.getLogger("arena")

router = APIRouter()


async def _run_suite(run_id: str, suite_name: str) -> None:
    """Background task: run every prompt in a suite, judge, tally.

    Sequential (not parallel) so slow providers don't stampede rate limits.
    Errors on individual prompts are recorded but don't abort the run.
    """
    config = runtime.config
    store = runtime.store
    suite = runtime.suites.get(suite_name)
    if not suite:
        await store.finish_suite_run(run_id, "errored", 0.0)
        return

    judge_model = config.judge_model()
    total_cost = 0.0
    status = "completed"

    for prompt in suite.prompts:
        try:
            model_a, model_b = select_models(config, suite.category)
        except ValueError as e:
            await store.record_suite_battle(run_id, prompt.id, None, None, str(e))
            continue

        battle_id = await store.create_battle(prompt.prompt, suite.category, model_a.id, model_b.id)
        try:
            results = await run_battle_headless(config, store, battle_id)
        except Exception as e:
            log.exception("suite %s prompt %s: battle failed", suite_name, prompt.id)
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"battle: {e}")
            continue

        err_a = results["a"].get("error")
        err_b = results["b"].get("error")
        if err_a or err_b:
            msg = f"a: {err_a}" if err_a else ""
            msg += (" | " if err_a and err_b else "") + (f"b: {err_b}" if err_b else "")
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, msg)
            continue

        total_cost += results["a"]["cost"] + results["b"]["cost"]

        if not judge_model or not config.judge:
            # No judge → skip the vote, record the battle unfinished. Operator
            # can still vote manually later; the suite run just carries no
            # winner for this prompt.
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, "no judge configured")
            continue

        try:
            verdict = await run_judge(
                config,
                config.judge,
                judge_model,
                prompt.prompt,
                results["a"]["response"],
                results["b"]["response"],
            )
            total_cost += verdict["cost"]
        except JudgeError as e:
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"judge: {e}")
            continue

        try:
            await store.record_vote(
                battle_id,
                verdict["winner"],
                method="judge",
                judge_reasoning=verdict["reasoning"],
                judge_model_id=verdict["judge_model_id"],
                judge_cost=verdict["cost"],
            )
        except ValueError as e:
            await store.record_suite_battle(run_id, prompt.id, battle_id, None, f"vote: {e}")
            continue

        await store.record_suite_battle(run_id, prompt.id, battle_id, verdict["winner"], None)

    await store.finish_suite_run(run_id, status, total_cost)
    record_suite_run_completed(suite_name)
    log.info("suite %s run %s done: $%.4f", suite_name, run_id, total_cost)


@router.get("/api/suites")
async def list_suites_route():
    """List all suites the server picked up at startup."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "prompt_count": len(s.prompts),
        }
        for s in runtime.suites.values()
    ]


@router.get("/api/suites/{name}")
async def get_suite_route(name: str):
    suite = runtime.suites.get(name)
    if not suite:
        raise HTTPException(404, f"suite not found: {name}")
    return {
        "name": suite.name,
        "description": suite.description,
        "category": suite.category,
        "prompts": [{"id": p.id, "prompt": p.prompt} for p in suite.prompts],
    }


@router.post("/api/suites/{name}/run")
async def run_suite_route(name: str):
    """Kick off a background run of the named suite; returns a run_id to poll."""
    suite = runtime.suites.get(name)
    if not suite:
        raise HTTPException(404, f"suite not found: {name}")
    if not runtime.config.judge_model():
        raise HTTPException(400, "suite runs require a configured judge (see models.yaml)")
    run_id = await runtime.store.create_suite_run(name, len(suite.prompts))
    record_suite_run_started(name)
    asyncio.create_task(_run_suite(run_id, name))
    return {"run_id": run_id, "battles_total": len(suite.prompts), "status": "running"}


@router.get("/api/suites/{name}/runs")
async def list_suite_runs_route(name: str):
    if name not in runtime.suites:
        raise HTTPException(404, f"suite not found: {name}")
    return await runtime.store.list_suite_runs(name)


@router.get("/api/suites/runs/{run_id}")
async def get_suite_run_route(run_id: str):
    run = await runtime.store.get_suite_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run
