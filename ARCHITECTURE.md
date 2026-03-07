# Open Model Arena — Architecture Guide

How the app works, end to end. Written so you can walk someone through it confidently.

---

## Stack

| Layer | Tech | Why |
|---|---|---|
| Backend | Python 3.12 / FastAPI | Async-native, great for streaming, minimal boilerplate |
| Frontend | Vanilla JS + HTML + CSS | No build step, no framework dependency, ships as static files |
| Database | SQLite with WAL mode | Single-file DB, WAL allows concurrent reads during writes |
| Streaming | Server-Sent Events (SSE) | One-way server-to-client stream, simpler than WebSockets for this use case |
| AI Clients | OpenAI Python SDK | Works with any OpenAI-compatible endpoint (LiteLLM, Ollama, direct API, etc.) |
| Container | Docker (python:3.12-slim) | Consistent deployment, single `docker compose up` |
| Config | YAML (models.yaml) | Add/remove models and providers without touching code |

---

## How a Battle Works

### 1. User submits a prompt

The frontend POSTs to `/api/battle` with the prompt text, category, and optional model selections.

**`app/main.py` — `create_battle()`**
- Validates prompt (non-empty, max 10k chars)
- If user picked specific models, validates they exist and are different
- If "mystery match", calls `select_models()` to randomly pair two models
- Creates a battle record in SQLite, returns a `battle_id`

### 2. Model selection logic

**`app/arena.py` — `select_models()`**
- Pulls all enabled models for the chosen category
- 40% chance to include a local (Ollama) model if available
- Otherwise picks two gateway models
- Randomly swaps A/B position to prevent position bias

### 3. Streaming responses

The frontend opens an `EventSource` (SSE connection) to `/api/battle/{id}/stream`.

**`app/arena.py` — `stream_battle()`**
- Creates two async OpenAI clients (one per model)
- Fires both API calls concurrently using `asyncio.create_task()`
- Each task pushes tokens into an `asyncio.Queue` as they arrive
- The main loop polls both queues and yields SSE events:
  - `model_a` / `model_b` — individual tokens as they stream in
  - `model_a_done` / `model_b_done` — final stats (latency, token count, cost)
  - `model_a_error` / `model_b_error` — if a model call fails
  - `battle_complete` — both models finished, close the connection

**Why queues?** Each model streams at its own speed. The queues decouple the two streams so one slow model doesn't block the other. The main loop interleaves tokens from both, yielding them to the client as fast as they arrive.

**Why SSE instead of WebSockets?** SSE is one-way (server to client), which is all we need here. It's simpler to implement, works through proxies, and auto-reconnects. WebSockets would be overkill since the client never sends data during streaming.

### 4. Frontend rendering

**`static/app.js` — `streamBattle()`**
- Opens EventSource, listens for model_a/model_b events
- Each token gets appended to the response string
- `renderPanel()` runs the accumulated text through `marked.js` (Markdown parser) and `highlight.js` (syntax highlighting)
- A blinking cursor element follows the end of the stream
- When both models fire their `done` event, vote buttons appear

### 5. Voting and ELO

User clicks A WINS, B WINS, or TIE. Frontend POSTs to `/api/battle/{id}/vote`.

**`app/store.py` — `record_vote()`**
- Updates both the "overall" and category-specific ratings
- ELO calculation uses K-factor of 32 (standard for new ratings)
- The math: expected score = 1 / (1 + 10^((opponent_rating - my_rating) / 400))
- Winner gains K * (1 - expected), loser drops K * (0 - expected)
- Ties give each player K * (0.5 - expected)
- Logs the vote with before/after ratings for audit

**`app/store.py` — `_update_elo()`**
- Pure function, no side effects
- Same algorithm used by chess (FIDE), adapted for model comparison

### 6. Reveal

After voting, the backend returns both model identities, latency, token counts, cost, and ELO changes. The frontend shows the reveal view with model names, provider badges, and the ELO delta.

---

## Key Design Decisions

**OpenAI-compatible client for everything.** Any OpenAI-compatible endpoint works — LiteLLM, Ollama, direct API access, or a self-hosted gateway. One client library (`openai` Python SDK) talks to all providers. Adding a new provider is just a new entry in `models.yaml` with a `base_url`.

**Config-driven models.** `models.yaml` defines providers (endpoints + API keys) and models (which provider, cost, categories). No code changes needed to add or remove models.

**WAL mode on SQLite.** Write-Ahead Logging allows reads to happen while writes are in progress. Important because SSE streams are long-running reads while votes are writes. Without WAL, a vote could block an active stream.

**Blind by default.** Model identities are hidden until after voting to prevent bias. The backend stores model IDs from the start but the frontend only shows "Model A" and "Model B" until the reveal.

**Tied rankings.** Models with identical ELO share the same rank number on the leaderboard instead of being arbitrarily ordered.

**Identical response handling.** If both models return the exact same text, only the TIE button is enabled since there's no meaningful difference to judge.

---

## File Map

```
app/
  main.py      — FastAPI routes, app lifecycle, static file serving
  arena.py     — Model selection, streaming battle logic, cost estimation
  store.py     — SQLite operations, ELO updates, vote logging
  config.py    — YAML config loader, dataclasses for Provider/Model/Config
  models.py    — Pydantic request/response schemas

static/
  index.html   — Single page with all views (arena, battle, reveal, leaderboard)
  app.js       — Frontend logic: streaming, voting, leaderboard, routing
  style.css    — All styling including responsive/mobile

models.yaml    — Provider endpoints and model definitions
Dockerfile     — Python 3.12-slim, pip install, copy app
docker-compose.yml — Container config, port mapping, volume for DB
```

---

## Rate Limiting

The app uses a simple in-memory rate limiter on battle creation (the expensive operation since it makes 2 API calls). Default: 10 battles per minute per IP. This prevents accidental or intentional API key burn without adding external dependencies.

Implementation: `app/ratelimit.py` — sliding window counter keyed by client IP. Applied as middleware on the `/api/battle` POST endpoint.

---

## What Would Change for Production

If this ever moved beyond proof-of-concept:

1. **Authentication** — OAuth or API key per user, not open access
2. **Rate limiting** — Already added (see above), but would want persistent storage (Redis) at scale
3. **HTTPS** — Any reverse proxy works (nginx, Caddy, Cloudflare Tunnel). Auth cookies require HTTPS; localhost is exempt.
4. **Database** — SQLite works great for single-server, but PostgreSQL for multi-instance
5. **Observability** — Structured logging, request tracing, cost dashboards
6. **SSO** — Passphrase auth is fine for small teams; larger deployments would want OAuth or SAML
