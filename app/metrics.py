"""Prometheus metrics exposed at ``/metrics``.

Every counter/histogram is created once at import; the rest of the app calls
the small helpers below (``record_battle_started`` etc.) so wiring points
stay explicit and greppable.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Battle lifecycle -----------------------------------------------------------

battles_started = Counter(
    "arena_battles_started_total",
    "Battles created (regardless of completion).",
    labelnames=("category",),
)

battles_completed = Counter(
    "arena_battles_completed_total",
    "Battles where both models produced a non-error response.",
    labelnames=("category",),
)

battles_errored = Counter(
    "arena_battles_errored_total",
    "Battles where at least one model errored or timed out.",
    labelnames=("category", "side"),
)

# Votes ----------------------------------------------------------------------

votes_total = Counter(
    "arena_votes_total",
    "Votes cast, split by method (human/judge) and winner (a/b/tie).",
    labelnames=("method", "winner"),
)

# Cost + latency per model --------------------------------------------------

model_latency_seconds = Histogram(
    "arena_model_latency_seconds",
    "End-to-end response time per model call.",
    labelnames=("model_id", "provider"),
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0),
)

model_cost_dollars = Counter(
    "arena_model_cost_dollars_total",
    "Cumulative provider cost per model.",
    labelnames=("model_id", "provider"),
)

model_tokens_total = Counter(
    "arena_model_tokens_total",
    "Cumulative output tokens per model.",
    labelnames=("model_id", "provider"),
)

# Judge ----------------------------------------------------------------------

judge_votes = Counter(
    "arena_judge_votes_total",
    "Votes cast automatically by the judge.",
    labelnames=("judge_model_id",),
)

judge_cost_dollars = Counter(
    "arena_judge_cost_dollars_total",
    "Cumulative judge model cost.",
    labelnames=("judge_model_id",),
)

# Suite runs -----------------------------------------------------------------

suite_runs_started = Counter(
    "arena_suite_runs_started_total",
    "Suite runs kicked off.",
    labelnames=("suite_name",),
)

suite_runs_completed = Counter(
    "arena_suite_runs_completed_total",
    "Suite runs that finished (regardless of per-prompt errors).",
    labelnames=("suite_name",),
)

# Server -----------------------------------------------------------------

active_streams = Gauge(
    "arena_active_streams",
    "Currently open SSE battle streams.",
)


# --- Recording helpers (keep call sites tiny + typed) ---------------------


def record_battle_started(category: str) -> None:
    battles_started.labels(category=category or "unknown").inc()


def record_battle_side_completed(
    category: str, model_id: str, provider: str, latency_ms: int, cost: float, tokens: int
) -> None:
    model_latency_seconds.labels(model_id=model_id, provider=provider).observe(latency_ms / 1000.0)
    model_cost_dollars.labels(model_id=model_id, provider=provider).inc(cost)
    model_tokens_total.labels(model_id=model_id, provider=provider).inc(tokens)


def record_battle_side_errored(category: str, side: str) -> None:
    battles_errored.labels(category=category or "unknown", side=side).inc()


def record_battle_completed(category: str) -> None:
    battles_completed.labels(category=category or "unknown").inc()


def record_vote(method: str, winner: str, judge_model_id: str | None = None, judge_cost: float | None = None) -> None:
    votes_total.labels(method=method, winner=winner).inc()
    if method == "judge" and judge_model_id:
        judge_votes.labels(judge_model_id=judge_model_id).inc()
        if judge_cost:
            judge_cost_dollars.labels(judge_model_id=judge_model_id).inc(judge_cost)


def record_suite_run_started(name: str) -> None:
    suite_runs_started.labels(suite_name=name).inc()


def record_suite_run_completed(name: str) -> None:
    suite_runs_completed.labels(suite_name=name).inc()
