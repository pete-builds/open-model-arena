---
title: Auto-Deploy on New Release
description: A three-stage pattern that auto-updates a container from GHCR the moment a new tag lands. Verified, healthchecked, and alerted.
---

Arena's release pipeline publishes a multi-arch container image to GHCR
and a SLSA build-provenance attestation on every `v*.*.*` tag. If your
host follows the three-stage pattern below, a new release turns into a
live container with zero manual steps.

## The stages

1. **Release workflow bumps the public compose file.** `release.yml` runs
   `sed` on `docker-compose.yml` to point at the new version. That commit
   goes back to `main` with `[skip ci]`. Anyone who does `git pull` picks
   up the new pin.
2. **Host-specific override** (`docker-compose.override.yml`, gitignored)
   keeps the actual pin your host is running. Docker Compose merges it in
   automatically. Never edit the base `docker-compose.yml` on the host —
   past drift bugs traced back to that.
3. **Cron-driven auto-deploy script** on the host watches GHCR for a new
   semver tag, sed-bumps the override, does `docker compose pull && up
   -d`, waits for `HEALTHCHECK` to go green, and pings Discord. On
   unhealthy: restores the backup override, restarts, re-checks, pings 🔴.

## What ships in this repo

- `docker-compose.yml` — image-based (pinned tag, auto-bumped by
  `release.yml`)
- `docker-compose.dev.yml` — override for local build-from-source
- `deploy/autodeploy.env.example` — template for the per-service config
  the auto-deploy script sources
- `deploy/docker-compose.override.example.yml` — starter host-specific
  override with an image pin and optional host-networking snippet
- `release.yml` — publishes the image, attests provenance (SLSA v1,
  cosign keyless via OIDC — no long-lived keys), and auto-bumps the
  compose example

## Verifying the attestation

Every image tag ships with a SLSA build-provenance attestation as a
GHCR registry referrer. Verify from any host with `cosign` v3+:

```bash
cosign verify-attestation \
  --type slsaprovenance1 \
  --certificate-identity-regexp \
    '^https://github.com/pete-builds/open-model-arena/.github/workflows/release.yml@refs/tags/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/pete-builds/open-model-arena@<digest>
```

Use `--type slsaprovenance1` (cosign v3), not the bare `slsaprovenance`
alias — the attestation is v1.0.

## Onboarding a host

Assumes: nix1-style deploy host with the generic auto-deploy script
already installed (see `~/scripts/mcp-autodeploy.sh` — one script, many
services). New services just drop a config file + a staggered cron entry.

1. `git clone` the repo into `~/docker/model-arena/`
2. `cp deploy/docker-compose.override.example.yml docker-compose.override.yml`
   (fill in the image pin)
3. `cp .env.example .env`, fill in `ARENA_PASSPHRASE` + `AUTH_TOKEN_SECRET`
   (+ optionally `ARENA_API_TOKENS` for headless / metrics)
4. `docker compose up -d`; visit the host at port 3694
5. `cp deploy/autodeploy.env.example ~/scripts/mcp-autodeploy/model-arena.env`,
   `chmod 600`, edit the Discord webhook URL
6. Add a staggered cron entry (existing services live at `:05`, `:10`, etc.
   past 5am; pick a fresh minute):
   ```
   30 5 * * * /home/pete/scripts/mcp-autodeploy.sh model-arena \
     >> /home/pete/scripts/mcp-autodeploy/model-arena.log 2>&1
   ```
7. Smoke test: `bash ~/scripts/mcp-autodeploy.sh model-arena` — should
   exit silently with "Already on latest"

## Notification philosophy

The auto-deploy script is deliberately quiet on no-ops. Discord pings
only fire on:

- ✅ Successful deploy (new version live + healthy)
- 🔴 Failed deploy (rolled back to previous)
- 🚨 Rollback failed (needs hands-on)
- Container unhealthy without a deploy attempt

Daily "everything fine" runs produce zero noise. That's the point — if
Discord pings, something happened.
