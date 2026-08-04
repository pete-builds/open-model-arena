---
title: Threat Model
description: What Arena defends against, what it doesn't, and how to deploy it responsibly.
---

Arena is designed to run inside a trusted network — a homelab, a team VPN,
a Tailnet — behind an HTTPS reverse proxy, with a shared passphrase gating
access. It is *not* designed as a public multi-tenant service.

## In scope

- **Passphrase auth.** Compared with `hmac.compare_digest`. Never logged.
  Cookies are `HttpOnly`, `Secure`, `SameSite=Lax`; tokens are HMAC-SHA256
  signed with `AUTH_TOKEN_SECRET`.
- **CSRF.** Every mutating request validates a double-submit cookie
  (`arena_csrf` cookie + `X-CSRF-Token` header) with `hmac.compare_digest`.
- **Bearer tokens.** Constant-time compare against every allowed token —
  no position leak, no short-circuit.
- **API-key handling.** Provider keys read from env vars via
  `api_key_env`. Never rendered to the browser, logged, or included in
  exports.
- **Rate limiting.** Battle creation capped at 10 req/min/IP (respects
  `X-Forwarded-For` when behind a trusted proxy).
- **Prompt length cap.** 10,000 characters to bound per-request cost.
- **Judge response cap.** Response text truncated to 8k chars per side
  before reaching the judge — a runaway model can't blow the judge
  context.
- **HTTPS-required cookies.** Auth cookies set `Secure=True`. Remote
  deploys must terminate TLS at the reverse proxy. `localhost` is exempt.

## Out of scope

- **Multi-tenant isolation.** Everyone holding the passphrase (or a
  bearer token) sees the same battles, exports, and leaderboard. This is
  by design — Arena is a "one team, one arena" tool.
- **Prompt-injection defenses.** Prompts pass to model providers
  verbatim. If a model is configured with tool-use, function-calling, or
  agentic capabilities that could act on prompt content, that risk lives
  in your provider configuration, not in Arena.
- **DoS protection under distributed load.** The rate limiter is
  in-memory per-process. For a scaled deployment behind multiple workers,
  put a real rate limiter in front (nginx `limit_req`, Cloudflare, etc.).
  SSE streams hold connections open; reverse-proxy timeouts and
  connection caps are the operator's job.
- **Judge trustworthiness.** A judge model can self-favor by style. Arena
  hides identity labels (A/B only) and pins temperature at 0.0, but
  doesn't guarantee neutrality. For high-stakes evaluation, spot-check
  judge decisions manually or run multiple judges.

## Bearer-token specifics

- Bearer tokens are equivalent to full API access. They can create
  battles, cast votes, run suites, and read every endpoint EXCEPT the
  login page (which needs a passphrase, not a token).
- Bearer requests skip CSRF because bearer tokens aren't carried on
  cross-site navigations. Requiring CSRF on bearer would break `curl` and
  `requests` clients for no security gain.
- Rotate with the standard "add new, migrate clients, remove old" flow.
  `ARENA_API_TOKENS` accepts a comma-separated list; every value is
  compared with `hmac.compare_digest` against every allowed token.
- No token scoping yet. A token that can read `/api/costs` can also POST
  `/api/battle`. See [SHOWCASE-PLAN.md](https://github.com/pete-builds/open-model-arena/blob/main/docs/SHOWCASE-PLAN.md)
  for the scoped-tokens roadmap.

## Reporting a vulnerability

If you find a security issue, please **do not** open a public GitHub
issue. Open a private security advisory:

<https://github.com/pete-builds/open-model-arena/security/advisories/new>

Response within 7 days. Include: description + impact, reproduction
steps, affected version (image tag or commit SHA), and suggested
mitigation if you have one.

## Coordinated disclosure

If a fix requires more than 7 days, you'll be updated and credited
(unless you'd prefer otherwise) in the release notes for the version
that ships the fix.
