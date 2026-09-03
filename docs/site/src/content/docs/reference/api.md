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
| `POST` | `/api/battle` | Start a battle. Body: `{prompt, category, model_a?, model_b?, reasoning_effort?}`. Supplying one model draws a random opponent. `reasoning_effort` is `low`, `medium`, `high`, or omitted/`off`. Returns `{battle_id, reasoning_effort}`. Rate-limited to 10/min/IP. |
| `GET` | `/api/battle/{id}/stream` | Server-Sent Events. Emits `model_a`, `model_b`, `model_a_thinking`, `model_b_thinking`, `model_a_notice`, `model_b_notice`, `model_a_done`, `model_b_done`, `battle_complete`. The `*_done` payload carries `reasoning_effort` (as applied) and `reasoning_tokens`. |
| `POST` | `/api/battle/{id}/vote` | Body: `{winner: "a"|"b"|"tie"}`. Returns the full reveal payload + ELO deltas. |
| `POST` | `/api/battle/{id}/judge` | Runs the configured judge, casts an automated vote, returns the reveal payload + judge reasoning. See [LLM-as-Judge](/guides/judge-mode/). |
| `GET` | `/api/battle/{id}` | Permalink read. Only voted battles are returned; unvoted / in-flight is `404`. Includes ELO deltas + `vote_method` + judge reasoning if judged. |

## Audience polls

Presenter side, behind the normal auth gate:

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/battle/{id}/poll` | Open (or fetch) the poll for a finished, unvoted battle. Returns `{code, join_path, status, tally}`. `400` before both responses land, `409` once voted. |
| `GET` | `/api/battle/{id}/poll` | Live tally `{a, b, tie, total}` for the presenter screen. |
| `POST` | `/api/battle/{id}/poll/close` | Closes the poll and records the plurality as the vote (`vote_method: "audience"`). Returns the reveal payload plus `audience_tally`. `400` with zero votes, `409` if already closed or voted by hand. |

Audience side, **public** (no passphrase, no CSRF). Nothing here can create a battle or reach a provider:

| Method | Path | Notes |
|---|---|---|
| `GET` | `/vote/{code}` | Phone page. |
| `GET` | `/api/audience/{code}?voter_id=` | Prompt, both responses, `status` (`open`, `closed`, `expired`), `vote_count`, and the caller's `your_choice`. The tally and model names appear only once closed. |
| `POST` | `/api/audience/{code}/vote` | Body: `{voter_id, choice}`. `voter_id` is 8 to 64 chars of `[A-Za-z0-9_-]`, minted by the phone. Re-posting changes the vote. `409` once closed, `429` past 300/min per IP. |

## Leaderboard + stats

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/leaderboard?category=overall` | Ranked models, with provisional entries appended. Provisional threshold is 5 battles. Tied ELO shares a rank. |
| `GET` | `/api/stats` | `{total_battles, total_voted, battles_today}`. |
| `GET` | `/api/models` | Enabled models with categories, provider, and `reasoning` (`auto`, `yes`, `off`). |
| `GET` | `/api/features` | Server-side flags (`judge`, `suites`, `reasoning.efforts`, `audience`) so the frontend renders conditionally. |
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
