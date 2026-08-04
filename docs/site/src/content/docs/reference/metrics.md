---
title: Metrics
description: The Prometheus counters, histograms, and gauges Arena exposes at /api/metrics.
---

Arena exposes standard Prometheus text-format metrics at `/api/metrics`.
Gated by the same auth as the rest of `/api/*` — use a bearer token in the
Prometheus scrape config. See [Headless / CI API](/guides/headless-api/)
for the scrape config example.

## Counters

| Name | Labels | Meaning |
|---|---|---|
| `arena_battles_started_total` | `category` | Battles created (regardless of completion) |
| `arena_battles_completed_total` | `category` | Battles where both sides produced non-error responses |
| `arena_battles_errored_total` | `category, side` | Battles where a side (a/b) errored or timed out |
| `arena_votes_total` | `method, winner` | Votes cast (`method` = `human` or `judge`; `winner` = `a`/`b`/`tie`) |
| `arena_model_cost_dollars_total` | `model_id, provider` | Cumulative provider cost per model |
| `arena_model_tokens_total` | `model_id, provider` | Cumulative output tokens per model |
| `arena_judge_votes_total` | `judge_model_id` | Votes cast by the judge |
| `arena_judge_cost_dollars_total` | `judge_model_id` | Cumulative judge cost |
| `arena_suite_runs_started_total` | `suite_name` | Suite runs kicked off |
| `arena_suite_runs_completed_total` | `suite_name` | Suite runs that finished |

## Histograms

| Name | Labels | Meaning |
|---|---|---|
| `arena_model_latency_seconds` | `model_id, provider` | End-to-end response time per model call. Buckets: 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160 seconds. |

## Gauges

| Name | Meaning |
|---|---|
| `arena_active_streams` | Currently open SSE battle streams |

## Recording pattern

Metrics are wired via tiny helpers in `app/metrics.py`
(`record_battle_started(cat)`, `record_vote(method, winner, ...)`, etc.)
so the call sites in `app/main.py` stay explicit and greppable — search
for `record_*` to find every instrumentation point.

## Not exposed

Anything private:

- Never: passphrase, auth secret, provider API keys, bearer tokens.
- Never: prompt text, response text, per-user info. Cardinality risk aside,
  none of that belongs in metrics.

## Suggested alerts

A couple of starter rules — tune to your team.

```yaml
groups:
  - name: model-arena
    rules:
      - alert: ArenaJudgeSpendHigh
        expr: rate(arena_judge_cost_dollars_total[1h]) > 0.10
        for: 15m
        annotations:
          summary: Judge is burning > $0.10/hour ({{ $value }})

      - alert: ArenaModelErrorSpike
        expr: rate(arena_battles_errored_total[10m]) > 0.5
        for: 10m
        annotations:
          summary: >
            More than half of battles erroring for {{ $labels.category }}
            side {{ $labels.side }}

      - alert: ArenaSlowLocalModel
        expr: >
          histogram_quantile(0.95,
            rate(arena_model_latency_seconds_bucket[10m])
          ) > 30
        for: 15m
        annotations:
          summary: p95 latency > 30s for {{ $labels.model_id }}
```
