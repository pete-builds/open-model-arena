---
title: Changelog
description: All notable changes to Open Model Arena.
---

The canonical changelog lives in the repo as
[`CHANGELOG.md`](https://github.com/pete-builds/open-model-arena/blob/main/CHANGELOG.md).
This page mirrors the highlights for docs-site readers who don't want to
click through.

## 0.2.0 — 2026-09-03

- Prometheus `/api/metrics` + `/api/costs` dashboard
- Eval suites (YAML prompt sets, batch runs, per-run tally)
- LLM-as-judge (automated evaluator with human override)
- Bearer-token API auth for headless / CI use
- Battle permalinks (shareable URL for every completed battle)
- Docs site (this thing)
- Release workflow for tag-driven GHCR publish
- Thinking selector (`reasoning_effort`) with per-model `reasoning:` pinning
- Pick one model, draw the other
- Audience vote from phones: public `/vote/<code>`, QR join, live tally
- `TRUSTED_PROXIES` gates X-Forwarded-For; `COOKIE_SECURE` is now configurable

## 0.1.0 — 2026-08-03

Initial public release.

- Blind side-by-side model comparison with concurrent SSE streaming
- ELO leaderboard, per-category, with provisional thresholds and tied ranks
- Passphrase auth with HMAC-signed cookies + CSRF double-submit
- Config-driven providers and models (`models.yaml`)
- OpenAI-compatible client — works with any endpoint
- SQLite with WAL mode; vote audit log
- Fixed a TOCTOU race in `record_vote` (atomic conditional claim)

See [CHANGELOG.md](https://github.com/pete-builds/open-model-arena/blob/main/CHANGELOG.md)
for the full detail.
