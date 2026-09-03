"""Response-shape helpers shared by the vote, judge, permalink, and poll routes."""

from __future__ import annotations

import json

from .config import Config


def reveal_payload(config: Config, battle: dict, **extra) -> dict:
    """Model identities plus per-side stats for a finished battle.

    ``extra`` is merged last so callers can add Elo deltas, judge fields, or
    an audience tally without each route re-listing the base keys.
    """
    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])
    payload = {
        "model_a_id": battle["model_a"],
        "model_a_name": model_a.display_name if model_a else battle["model_a"],
        "model_a_provider": model_a.provider_name if model_a else "unknown",
        "model_b_id": battle["model_b"],
        "model_b_name": model_b.display_name if model_b else battle["model_b"],
        "model_b_provider": model_b.provider_name if model_b else "unknown",
        "latency_a_ms": battle["latency_a_ms"],
        "latency_b_ms": battle["latency_b_ms"],
        "tokens_a": battle["tokens_a"],
        "tokens_b": battle["tokens_b"],
        "cost_a": battle["cost_a"],
        "cost_b": battle["cost_b"],
        "reasoning_effort": battle.get("reasoning_effort"),
    }
    payload.update(extra)
    return payload


def parse_tally(raw: str | None) -> dict | None:
    """Decode the JSON audience tally stored on a vote_log row."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
