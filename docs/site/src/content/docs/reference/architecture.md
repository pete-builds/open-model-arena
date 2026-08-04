---
title: Architecture
description: How Open Model Arena is put together, end to end. Written so you can walk someone through it confidently.
---

Open Model Arena is small on purpose: a single FastAPI process, vanilla-JS
frontend with no build step, SQLite for storage, Docker for deploy.
Everything else — providers, models, judges, eval suites, alerting — hangs
off config files or standard HTTP.

## Stack

| Layer | Tech | Why |
|---|---|---|
| Backend | Python 3.12 / FastAPI | Async-native, great for streaming, minimal boilerplate |
| Frontend | Vanilla JS + HTML + CSS | No build step, no framework, ships as static files |
| Database | SQLite with WAL mode | Single file; WAL lets reads happen during writes |
| Streaming | Server-Sent Events | One-way, works through proxies, auto-reconnects |
| AI clients | OpenAI Python SDK | Any OpenAI-compatible endpoint speaks it |
| Container | Docker (python:3.12-slim) | Single `docker compose up` |
| Config | YAML (`models.yaml`, `suites/*.yaml`) | Add / remove models and suites without touching code |
| Metrics | prometheus_client | Standard scrape format |

## File map

```
app/
  main.py       — FastAPI routes, middleware, lifespan
  arena.py      — Model selection + streaming + headless battle runner
  auth.py       — HMAC cookie tokens + bearer API tokens
  config.py     — YAML loader, Provider / Model / Judge / Config dataclasses
  judge.py      — LLM-as-judge: prompt building + tolerant JSON verdict parsing
  metrics.py    — Prometheus counters + histograms + record helpers
  models.py     — Pydantic request/response schemas
  ratelimit.py  — In-memory sliding-window rate limiter
  store.py      — SQLite operations, ELO updates, vote / suite / cost queries
  suites.py     — Eval-suite YAML loader

static/
  index.html    — Single page: arena, battle, reveal, leaderboard views
  login.html    — Passphrase login form
  js/
    app.js      — Init, router, feature detection, share button
    battle.js   — Streaming, voting, judge invocation, reveal
    leaderboard.js — Leaderboard load + render
    templates.js  — Save / load prompt templates from localStorage
    state.js    — Shared state + DOM helpers
    theme.js    — Light / dark toggle

suites/*.yaml   — Codified eval prompt sets (loaded on startup)
models.yaml     — Providers, models, judge
```

## Request flow: one blind battle

1. **Prompt submitted.** Frontend POSTs `/api/battle`. Rate limiter checks
   the client IP; middleware confirms auth (cookie or bearer). If the user
   picked specific models, they're validated; otherwise `select_models()`
   picks two, biased to include a gateway model against a local one 40%
   of the time.
2. **Battle row inserted** into SQLite with an opaque 16-char ID.
3. **Frontend opens SSE** to `/api/battle/{id}/stream`. The server starts
   two `AsyncOpenAI` calls concurrently via `asyncio.create_task()`, each
   pushing tokens into its own `asyncio.Queue`. A main loop drains both
   queues and yields interleaved `model_a` / `model_b` events. Queues are
   the key: one slow model doesn't block the other's stream.
4. **Both models done.** Server yields `model_a_done` + `model_b_done`
   events with final latency / tokens / cost, and closes with
   `battle_complete`. Responses are persisted to the battle row.
5. **User votes.** Frontend POSTs `/api/battle/{id}/vote` (or clicks the
   judge button, which POSTs to `/judge`). The store atomically claims
   the winner via a conditional `UPDATE ... WHERE winner IS NULL` (see
   [ELO Math](/reference/elo-math/) for why this matters).
6. **Reveal.** Response includes model identities, ELO deltas, and if it
   was a judge vote, the judge's reasoning + cost.

## Suite runs

Suite runs pipeline the same primitives without SSE:

1. `POST /api/suites/{name}/run` creates a `suite_runs` row and fires an
   `asyncio.create_task()` — the HTTP request returns immediately with a
   `run_id`.
2. The task walks prompts sequentially. Each prompt: `select_models()` +
   `create_battle()` + `run_battle_headless()` (the non-streaming twin
   of `stream_battle`) + `run_judge()` + `record_vote(method="judge")`.
3. Per-prompt rows land in `suite_battles`; run-level status + cost
   updates on `suite_runs`.
4. Client polls `/api/suites/runs/{run_id}` for progress and final tally.

Sequential (not parallel) so slow providers don't stampede rate limits
from a single suite.

## Auth

- **Browser** — passphrase → HMAC-signed `arena_token` cookie + `arena_csrf`
  cookie for CSRF double-submit. Any state-changing POST must include the
  CSRF cookie AND matching `X-CSRF-Token` header.
- **Bearer** — `ARENA_API_TOKENS` env var (comma-separated). Any `/api/*`
  route accepts `Authorization: Bearer <token>` or `X-API-Token`. Bearer
  skips CSRF (bearer isn't carried on cross-site navigations).

The bearer path runs BEFORE the cookie path — a request carrying both
still works. If `ARENA_API_TOKENS` is empty, bearer is skipped entirely.

## What "reproducible" means here

- SQLite is the audit-of-record. Cost, votes, judge reasoning, ELO
  deltas — all recoverable from the DB alone.
- Prometheus is derived state; scrape loss doesn't rewrite the ledger.
- Suite runs are keyed by an opaque `run_id` so a diff between runs is
  a well-defined operation.

## Non-goals

- Multi-server. One process, one SQLite file. If you need scale, run
  one arena per team.
- Multi-tenant. Everyone with the passphrase sees the same battles.
  See [Threat Model](/reference/threat-model/) for the boundary.
- Model hosting. Bring your own endpoint.
