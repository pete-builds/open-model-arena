# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Auto-deploy pipeline scaffolding.** `release.yml` grew SLSA v1
  build-provenance attestations (cosign keyless via OIDC — no long-lived
  keys) and an auto-bump step that rewrites the pinned image tag in
  `docker-compose.yml` on every tag push. `docker-compose.yml` now
  ships as image-based (pinned to `ghcr.io/pete-builds/open-model-arena`)
  so a `git pull` alone rolls forward; `docker-compose.dev.yml` is the
  local build-from-source override. `deploy/autodeploy.env.example` +
  `deploy/docker-compose.override.example.yml` document the nix1-side
  onboarding (per-service config, staggered cron, gitignored host
  override, Discord webhook). New docs page under
  `/guides/autodeploy/` walks the three-stage pattern and the cosign
  verification command.
- **Docs site.** New Astro + Starlight site under `docs/site/`, published
  to <https://pete-builds.github.io/open-model-arena/> via a `docs.yml`
  workflow on every `main` push that touches `docs/site/**`. Ships pages
  for: getting started, eval suites, LLM-as-judge, headless API,
  deployment (Docker + Caddy/nginx/Cloudflare/Tailscale), API reference,
  architecture, ELO math (formula + K + concurrency fix), threat model,
  and metrics (counter catalog + sample alerts).
- **Prometheus `/api/metrics` + `/api/costs` dashboard.** New
  `prometheus-client` dep exposes battle-lifecycle counters
  (`arena_battles_started_total`, `arena_votes_total` split by method +
  winner, `arena_model_cost_dollars_total` per model, latency
  histograms per model, judge cost/votes, suite runs). `/api/metrics`
  reuses the standard bearer/cookie gate so Prometheus scrapes with a
  bearer token in `authorization`. Separate `/api/costs?days=N` returns
  the audit-of-record breakdown from SQLite: per-model spend, share%,
  measured cost per 1k output tokens (from real API usage numbers, not
  configured pricing), and folds judge cost onto the judge model.
  8 new tests; suite goes from 140 to 146.
- **Eval suites.** YAML-defined prompt sets under `suites/` become the
  codified evals a team runs on demand. `GET /api/suites` lists them,
  `POST /api/suites/{name}/run` kicks off a background run that fires
  one battle per prompt, uses the configured judge to cast the vote
  automatically, and records aggregate results (per-run tally,
  per-battle rows, total cost, error surface) under a `run_id`.
  `GET /api/suites/runs/{run_id}` returns the full run detail for
  polling. Two new tables (`suite_runs`, `suite_battles`) added via the
  same additive-migration path used for the judge columns. New
  `app/suites.py` loader with strict validation (unique prompt IDs,
  required fields, one suite per file). New `run_battle_headless()` in
  `app/arena.py` so the runner reuses the exact same call/persist logic
  as the streaming path. Suite runs REQUIRE a configured judge
  (`400` otherwise) — the whole point is codified, hands-off eval.
  16 new tests; suite goes from 124 to 140. `suites/README.md` +
  `suites/example.yaml.example` document the format.
- **LLM-as-judge.** Optional `judge:` section in `models.yaml` designates
  any configured model as an automated evaluator. A "let \<judge\> decide"
  button appears in the battle view once both responses arrive; new
  `POST /api/battle/{id}/judge` runs the judge against a rubric (sensible
  default; overridable per config), records the verdict as a vote with
  `method="judge"`, and stores the reasoning + cost on the vote log for
  audit. The reveal view surfaces the judge's reasoning inline. Permalinks
  round-trip the method + reasoning. New `app/judge.py` (JSON verdict
  parser tolerates fenced / prose-wrapped replies), `app/config.py` gets
  `Judge` + `judge_model()`, `app/store.py` gains additive
  `method`/`judge_reasoning`/`judge_model_id`/`judge_cost` columns via
  idempotent `ALTER TABLE`, new `/api/features` endpoint so the frontend
  renders the button conditionally. 20 new tests (`test_judge.py` +
  integration tests in `test_api.py`); suite goes from 104 to 124.
- **API bearer tokens for headless / CI use.** New `ARENA_API_TOKENS` env
  var accepts a comma-separated list of tokens; every `/api/*` route
  accepts `Authorization: Bearer <token>` or the `X-API-Token` alias.
  Bearer-authenticated requests skip the CSRF double-submit that applies
  to cookie sessions (bearer tokens aren't carried on cross-site
  navigations). Constant-time comparison against every allowed token to
  avoid position-leaking. New `app/auth.py` module + `tests/test_auth.py`
  and five bearer-path integration tests in `tests/test_api.py`.
- **Battle permalinks.** Every completed battle now has a shareable URL. New
  `GET /api/battle/{id}` returns the full reveal payload (prompt, both
  responses, models, latency, tokens, cost, winner, ELO deltas) for voted
  battles only — unvoted or in-flight battles 404 so a share link never
  leaks a mid-stream state. The reveal view auto-updates the address bar
  to `/battle/<id>` on vote, adds a SHARE button that copies the URL, and
  the router rehydrates the reveal view when someone opens a permalink
  directly.
- `CHANGELOG.md` (this file)
- `docs/SHOWCASE-PLAN.md` — roadmap and audience framing for the
  showcase-elevation work
- Release workflow (`.github/workflows/release.yml`) — tag push builds and
  pushes `ghcr.io/pete-builds/open-model-arena:vX.Y.Z` and `:latest` for
  `linux/amd64` and `linux/arm64`, and creates a GitHub release with the
  matching changelog section as the body
- PEP 621 `[project]` metadata in `pyproject.toml` — name, version,
  description, keywords, classifiers, URLs, runtime + dev extras
- README badges (CI, license, container image, Python version) and a
  Roadmap section pointing at the plan

## [0.1.0] - 2026-08-03

Initial public release.

### Added

- Blind side-by-side model comparison with concurrent SSE streaming
- ELO leaderboard, filterable by category, with provisional thresholds
  (`MIN_BATTLES_FOR_RANKING = 5`) and tied-rank handling
- Targeted-comparison mode (skip mystery match, pick both models)
- Passphrase-based auth with HMAC-signed cookies + CSRF double-submit
- Per-response cost tracking, using real API usage numbers when the
  provider returns them
- Vote audit log with before/after ratings
- Markdown rendering with syntax highlighting; per-battle CSV/JSON export
- Prompt template save/load in localStorage; dark/light theme
- Rate limiting on battle creation (10/min/IP, sliding window)
- OpenAI-compatible client that works with any endpoint (Ollama,
  LiteLLM, direct API, self-hosted gateway)
- Config-driven providers and models via `models.yaml` — no code
  changes needed to add or remove a model
- SQLite with WAL mode for concurrent read-during-write on live streams

### Fixed

- Closed a TOCTOU race in `record_vote` where concurrent votes on the
  same battle could both pass the "already voted" check and double-count
  ELO. Replaced the read-then-write pattern with an atomic conditional
  `UPDATE ... WHERE winner IS NULL`; the ELO update and audit-log insert
  only run after a successful claim. Covered by a new concurrent test.

[Unreleased]: https://github.com/pete-builds/open-model-arena/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pete-builds/open-model-arena/releases/tag/v0.1.0
