"""Audience polls: let a room full of phones vote on a battle.

Two halves. The presenter half hangs off ``/api/battle/{id}/poll`` and sits
behind the normal passphrase / bearer gate. The audience half lives under
``/api/audience/{code}`` and ``/vote/{code}``, which the auth middleware
leaves open: students never see the passphrase, and nothing on that side
can create a battle or reach a model provider. All an audience caller can
do is read two finished responses and record one choice per device.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import runtime
from .clientip import get_client_ip
from .metrics import record_vote
from .models import AudienceVoteRequest
from .payloads import reveal_payload
from .store import POLL_STATUS_CLOSED, POLL_STATUS_OPEN

router = APIRouter()

_BATTLE_ID_RE = re.compile(r"^[a-zA-Z0-9]{16}$")
_POLL_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
_VOTER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _validate_battle_id(battle_id: str) -> None:
    if not _BATTLE_ID_RE.match(battle_id):
        raise HTTPException(400, "invalid battle ID format")


def _normalize_code(code: str) -> str:
    code = code.strip().upper()
    if not _POLL_CODE_RE.match(code):
        raise HTTPException(400, "invalid poll code")
    return code


def _poll_state(poll: dict, tally: dict) -> dict:
    return {
        "code": poll["code"],
        "battle_id": poll["battle_id"],
        "status": poll["status"],
        "join_path": f"/vote/{poll['code']}",
        "created_at": poll["created_at"],
        "closed_at": poll["closed_at"],
        "tally": tally,
    }


def majority(tally: dict) -> str:
    """Turn a tally into a vote: the plurality choice, with a/b deadlocks as a tie."""
    a, b, tie = tally.get("a", 0), tally.get("b", 0), tally.get("tie", 0)
    top = max(a, b, tie)
    if a == top and b == top:
        return "tie"
    if a == top:
        return "a"
    if b == top:
        return "b"
    return "tie"


# --- Presenter side (authenticated) ---


@router.post("/api/battle/{battle_id}/poll")
async def open_poll(battle_id: str):
    """Open (or fetch) the audience poll for a finished, unvoted battle."""
    _validate_battle_id(battle_id)
    battle = await runtime.store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")
    if battle.get("winner"):
        raise HTTPException(409, "battle already voted")
    if not battle.get("response_a") or not battle.get("response_b"):
        raise HTTPException(400, "both responses must complete before opening a poll")
    poll = await runtime.store.create_poll(battle_id)
    tally = await runtime.store.get_poll_tally(poll["code"])
    return _poll_state(poll, tally)


@router.get("/api/battle/{battle_id}/poll")
async def poll_status(battle_id: str):
    """Live tally for the presenter screen."""
    _validate_battle_id(battle_id)
    poll = await runtime.store.get_poll_for_battle(battle_id)
    if not poll:
        raise HTTPException(404, "no poll for this battle")
    tally = await runtime.store.get_poll_tally(poll["code"])
    return _poll_state(poll, tally)


@router.post("/api/battle/{battle_id}/poll/close")
async def close_poll(battle_id: str):
    """Close the poll and record its plurality as the battle's vote (method=audience)."""
    _validate_battle_id(battle_id)
    poll = await runtime.store.get_poll_for_battle(battle_id)
    if not poll:
        raise HTTPException(404, "no poll for this battle")
    if poll["status"] != POLL_STATUS_OPEN:
        raise HTTPException(409, f"poll is {poll['status']}")
    battle = await runtime.store.get_battle(battle_id)
    if not battle:
        raise HTTPException(404, "battle not found")
    if battle.get("winner"):
        raise HTTPException(409, "battle already voted")

    tally = await runtime.store.get_poll_tally(poll["code"])
    if tally["total"] == 0:
        raise HTTPException(400, "no audience votes yet")

    winner = majority(tally)
    if not await runtime.store.close_poll(poll["code"]):
        raise HTTPException(409, "poll already closed")
    try:
        elo_results = await runtime.store.record_vote(
            battle_id,
            winner,
            method="audience",
            audience_tally=json.dumps(tally),
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    record_vote("audience", winner)

    return reveal_payload(
        runtime.config,
        battle,
        vote_method="audience",
        audience_tally=tally,
        winner=winner,
        **elo_results,
    )


# --- Audience side (public) ---


@router.get("/vote/{code}")
async def vote_page(code: str):
    _normalize_code(code)
    return FileResponse("static/vote.html")


@router.get("/api/audience/{code}")
async def audience_poll(code: str, voter_id: str | None = None):
    """What a phone needs to render: the prompt, both responses, and the poll status.

    The tally stays hidden while the poll is open so early votes cannot pull
    later ones. Once closed, the model names and the final count come back.
    """
    code = _normalize_code(code)
    poll = await runtime.store.get_poll(code)
    if not poll:
        raise HTTPException(404, "poll not found")
    battle = await runtime.store.get_battle(poll["battle_id"])
    if not battle:
        raise HTTPException(404, "battle not found")

    tally = await runtime.store.get_poll_tally(code)
    payload = {
        "code": code,
        "status": poll["status"],
        "prompt": battle["prompt"],
        "category": battle["category"],
        "response_a": battle["response_a"] or "",
        "response_b": battle["response_b"] or "",
        "vote_count": tally["total"],
        "your_choice": None,
    }
    if voter_id and _VOTER_ID_RE.match(voter_id):
        payload["your_choice"] = await runtime.store.get_poll_voter_choice(code, voter_id)

    if poll["status"] == POLL_STATUS_CLOSED and battle.get("winner"):
        model_a = runtime.config.get_model(battle["model_a"])
        model_b = runtime.config.get_model(battle["model_b"])
        payload.update(
            {
                "winner": battle["winner"],
                "model_a_name": model_a.display_name if model_a else battle["model_a"],
                "model_b_name": model_b.display_name if model_b else battle["model_b"],
                "tally": tally,
            }
        )
    return payload


@router.post("/api/audience/{code}/vote")
async def audience_vote(code: str, req: AudienceVoteRequest, request: Request):
    code = _normalize_code(code)
    if not runtime.audience_limiter.is_allowed(get_client_ip(request)):
        raise HTTPException(429, "slow down")
    if not _VOTER_ID_RE.match(req.voter_id):
        raise HTTPException(400, "invalid voter_id")
    if req.choice not in ("a", "b", "tie"):
        raise HTTPException(400, "choice must be 'a', 'b', or 'tie'")
    try:
        await runtime.store.cast_poll_vote(code, req.voter_id, req.choice)
    except ValueError as e:
        msg = str(e)
        if msg == "poll not found":
            raise HTTPException(404, msg) from e
        raise HTTPException(409, msg) from e
    tally = await runtime.store.get_poll_tally(code)
    return {"ok": True, "choice": req.choice, "vote_count": tally["total"]}
