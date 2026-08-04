# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
