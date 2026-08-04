"""LLM-as-judge: ask a designated model to pick a winner between two responses.

The judge is called via the same OpenAI-compatible client the arena uses for
regular battles, so any endpoint (OpenAI, Ollama, LiteLLM, self-hosted gateway)
can serve as the judge. The judge sees both responses labelled A/B with the
original prompt, evaluates against the configured rubric, and returns a
compact JSON verdict.

The judge's model identity is deliberately hidden from itself when possible
(no ``system:`` role message reveals which vendor is A vs. B), so a
same-family judge cannot self-favor by name recognition.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from openai import AsyncOpenAI

from .arena import estimate_cost, get_client
from .config import Config, Judge, Model

log = logging.getLogger("arena.judge")

_MAX_JUDGE_OUTPUT_TOKENS = 512
# Cap the response text we show the judge so a runaway model can't blow the
# judge's context window. 8k chars is roughly ~2k tokens per response.
_MAX_JUDGE_INPUT_CHARS_PER_RESPONSE = 8000


class JudgeError(Exception):
    """Raised when the judge fails to produce a usable verdict."""


def _truncate(text: str, limit: int = _MAX_JUDGE_INPUT_CHARS_PER_RESPONSE) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [truncated]"


def _build_messages(prompt: str, response_a: str, response_b: str, rubric: str) -> list[dict[str, str]]:
    user_content = (
        f"PROMPT:\n{prompt}\n\n"
        f"RESPONSE A:\n{_truncate(response_a)}\n\n"
        f"RESPONSE B:\n{_truncate(response_b)}\n\n"
        "Return your verdict as JSON only."
    )
    return [
        {"role": "system", "content": rubric},
        {"role": "user", "content": user_content},
    ]


def _extract_verdict(raw: str) -> dict[str, str]:
    """Parse the judge's reply. Accepts plain JSON, JSON in a ```json fence,
    or JSON with surrounding prose (fallback: first {...} block)."""
    text = raw.strip()
    # Strip a leading ```json / ``` fence if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # If still not plain JSON, grab the first balanced-looking {...}
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise JudgeError(f"judge returned no JSON: {raw[:200]!r}")
        text = m.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise JudgeError(f"judge JSON parse failed: {e}: {text[:200]!r}") from e
    winner = str(parsed.get("winner", "")).lower()
    if winner not in ("a", "b", "tie"):
        raise JudgeError(f"judge winner must be 'a', 'b', or 'tie'; got {winner!r}")
    reasoning = str(parsed.get("reasoning", "")).strip()[:1000]
    return {"winner": winner, "reasoning": reasoning}


async def run_judge(
    config: Config,
    judge: Judge,
    judge_model: Model,
    prompt: str,
    response_a: str,
    response_b: str,
) -> dict[str, Any]:
    """Invoke the judge model and return {winner, reasoning, latency_ms, tokens, cost}."""
    if not response_a and not response_b:
        raise JudgeError("both responses empty; nothing to judge")

    client: AsyncOpenAI = get_client(config, judge_model)
    messages = _build_messages(prompt, response_a, response_b, judge.rubric)

    provider = config.get_provider(judge_model.provider_name)
    timeout_s = provider.timeout or 30

    start = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=judge_model.model_id,
            messages=messages,
            max_tokens=_MAX_JUDGE_OUTPUT_TOKENS,
            temperature=0.0,
            timeout=timeout_s,
        )
    except Exception as e:  # openai.APIError, httpx.TimeoutException, etc.
        log.exception("judge call failed")
        raise JudgeError(f"judge call failed: {e}") from e
    elapsed_ms = int((time.monotonic() - start) * 1000)

    text = (resp.choices[0].message.content or "") if resp.choices else ""
    verdict = _extract_verdict(text)

    usage = getattr(resp, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cost = estimate_cost(judge_model, config, prompt_tokens, completion_tokens)

    log.info(
        "judge %s → %s (%dms, %d+%d tok, $%.4f)",
        judge_model.id,
        verdict["winner"],
        elapsed_ms,
        prompt_tokens,
        completion_tokens,
        cost,
    )

    return {
        "winner": verdict["winner"],
        "reasoning": verdict["reasoning"],
        "latency_ms": elapsed_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": round(cost, 6),
        "judge_model_id": judge_model.id,
        "judge_display_name": judge_model.display_name,
    }
