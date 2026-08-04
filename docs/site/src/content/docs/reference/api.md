---
title: API Reference
description: Every /api route Open Model Arena exposes, with auth requirements and shape notes.
---

Every route is gated by either the browser cookie session (passphrase +
CSRF) or the [bearer token](/guides/headless-api/) auth path. Bearer
requests skip CSRF; both flow through the same middleware.

## Battles

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/battle` | Start a battle. Body: `{prompt, category, model_a?, model_b?}`. Returns `{battle_id}`. Rate-limited to 10/min/IP. |
| `GET` | `/api/battle/{id}/stream` | Server-Sent Events. Emits `model_a`, `model_b`, `model_a_done`, `model_b_done`, `battle_complete`. |
| `POST` | `/api/battle/{id}/vote` | Body: `{winner: "a"|"b"|"tie"}`. Returns the full reveal payload + ELO deltas. |
| `POST` | `/api/battle/{id}/judge` | Runs the configured judge, casts an automated vote, returns the reveal payload + judge reasoning. See [LLM-as-Judge](/guides/judge-mode/). |
| `GET` | `/api/battle/{id}` | Permalink read. Only voted battles are returned; unvoted / in-flight is `404`. Includes ELO deltas + `vote_method` + judge reasoning if judged. |

## Leaderboard + stats

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/leaderboard?category=overall` | Ranked models, with provisional entries appended. Provisional threshold is 5 battles. Tied ELO shares a rank. |
| `GET` | `/api/stats` | `{total_battles, total_voted, battles_today}`. |
| `GET` | `/api/models` | Enabled models with categories. |
| `GET` | `/api/features` | Server-side flags (`judge`, `suites`) so the frontend renders conditionally. |
| `GET` | `/api/export?format=csv\|json` | Full history of voted battles. |

## Suites

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/suites` | List suites loaded from `suites/*.yaml`. |
| `GET` | `/api/suites/{name}` | Suite detail including all prompts. |
| `POST` | `/api/suites/{name}/run` | Kicks off a background run. `400` if no judge is configured. Returns `{run_id, battles_total, status}`. |
| `GET` | `/api/suites/{name}/runs` | List runs of a suite (most-recent first, up to 20). |
| `GET` | `/api/suites/runs/{run_id}` | Full detail: status, cost, per-battle rows, per-model tally. |

## Costs + metrics

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/costs?days=N` | Per-model spend + measured cost per 1k output tokens. `days` in `[1, 3650]`. |
| `GET` | `/api/metrics` | Prometheus scrape endpoint. See [Metrics](/reference/metrics/). |

## Auth

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/login` | Body: `{passphrase}`. Sets `arena_token` + `arena_csrf` cookies. |
| `GET` | `/login` | HTML login page. Public. |
| `GET` | `/healthz` | `{"status": "ok"}`. Public. Container healthcheck target. |

## SPA routes

| Method | Path | Notes |
|---|---|---|
| `GET` | `/` | Arena view (SPA entry). |
| `GET` | `/leaderboard` | Leaderboard view (SPA entry). |
| `GET` | `/battle/{id}` | Permalink view (SPA entry). Rehydrates the reveal from `/api/battle/{id}`. |

## Error shape

`4xx` and `5xx` return `{"detail": "human-readable message"}`. Rate-limit
`429` returns `"slow down — max 10 battles per minute"`.
