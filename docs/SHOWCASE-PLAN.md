# Showcase Plan

Elevating Open Model Arena from "useful side project" to portfolio-caliber,
matched against the [mcp-unifi](https://github.com/pete-builds/mcp-unifi) bar:
signed releases, defensible threat model, docs site, and a signature capability
the audience actually needs.

## Audience

Three concentric rings, each with a slightly different ask.

| Ring | Who | What they want |
|---|---|---|
| **Inner** | Homelab AI folks with Ollama on a Mac Mini, LM Studio, vLLM, llama.cpp | "Is my local model actually competitive with what I'm paying Cloud X for?" |
| **Middle** | Small teams standing up an internal AI stack | "Which model do we ship to production for prompt shape Y, at what cost?" |
| **Outer** | AI platform / MLOps engineers picking model tiers behind a gateway | "Give me a reproducible eval I can point at any OpenAI-compatible endpoint." |

The current app serves the inner ring well. Everything below is aimed at
expanding into the middle and outer rings without alienating the inner one.

## Signature capability

mcp-unifi's signature is "safety-first agent tooling with dry-run + audit log."
Open Model Arena's signature will be:

> **"Actually run the eval — blind, cost-aware, and reproducible against any
> OpenAI-compatible endpoint."**

The differentiator against Chatbot Arena and public leaderboards is not the ELO
math; it's that a team can codify a prompt set that mirrors their real work, run
it against their models on their infrastructure, and get a defensible answer
including cost. That means eval suites and LLM-as-judge become first-class, not
bolted-on.

## Tranches

### Tranche 1 — Showcase scaffolding (repo polish, low code risk)

Bring the repo up to a showcase standard so a reviewer trusts the project
before reading a line of Python. Some pieces already shipped in the initial
`v0.1.0` (CI, Dependabot, CONTRIBUTING, SECURITY, pyproject tooling). This
tranche fills the rest of the gap.

- `CHANGELOG.md` — Keep a Changelog + SemVer, seeded with `0.1.0`
- Release workflow — tag push → multi-arch container image on GHCR + GitHub
  release with the matching changelog section as the body
- PEP 621 `[project]` block in `pyproject.toml` — name, version, keywords,
  classifiers, URLs, extras
- README badges (CI, license, container image) and a Roadmap section
- Expanded `SECURITY.md` with a full threat model (in-scope / out-of-scope,
  API-key handling, HTTPS-required cookies)
- Expanded `CONTRIBUTING.md` with the real dev loop and test norms

### Tranche 2 — Killer features (expand the audience)

Each feature independently justifiable; together they land the signature.

- **Eval suites** — YAML-defined prompt sets under `suites/`. Run a suite as
  a batch of battles across all enabled models. Suite results feed a
  suite-scoped leaderboard, separate from the free-play ELO so ad-hoc
  battles don't skew a codified eval.
- **LLM-as-judge** — designate a judge model in `models.yaml`. When judge
  mode is on, the judge scores the two responses against a rubric and casts
  the vote. Human override always available; audit log records who or what
  voted.
- **Cost dashboard** — cumulative spend, spend by model, cost per 1k output
  tokens (measured, not just configured), cost per ELO point, "cheapest
  model within N points of the leader."
- **Prometheus `/metrics`** — battles started, battles completed, votes by
  method (human/judge/tie), latency histograms per model, cumulative cost.
- **API tokens** — bearer tokens for programmatic use so CI can run an
  eval suite headlessly. Scoped tokens (read-only vs. run-suite vs. admin).
- **Battle permalinks** — every battle already has an ID; expose
  `/battle/<id>` as a public-with-token shareable link so a team can put a
  URL in Slack instead of a screenshot.
- **Reasoning trace toggle** — thinking models (DeepSeek R1, GPT-5.3,
  o-series) emit reasoning content separately. Show/hide toggle, and record
  reasoning tokens as a distinct cost bucket.
- **Fair-sampling controls** — the current 40% local bias helps free play
  but hurts codified evals. Add a "balanced" pairing mode that equalizes
  battles per model over a window.

### Tranche 3 — Docs site + demo

- Astro docs site under `docs/site/` (matches mcp-unifi), published to GH
  Pages
- Pages: Architecture, ELO math, Eval suites HOWTO, Judge mode HOWTO, API
  reference, Threat model, Deployment (Docker, Helm, Cloudflare Tunnel,
  Tailscale Funnel)
- Fresh screenshots for arena, leaderboard, session mode, cost dashboard,
  eval-suite run view
- Optional: a read-only public demo, gated behind a heavy per-IP rate
  limit, pointed at a small set of cheap models. Uses a demo API key with a
  hard monthly cap.

### Tranche 4 — Homelab auto-deploy

Reuse the three-stage GitHub release → compose auto-bump → cron + Discord
pattern already proven on mcp-unifi so nix1 self-updates the moment a tag
lands.

## Non-goals

- Model hosting or inference. The whole point is BYO endpoint.
- Multi-tenant SaaS. This is a single-team self-hosted tool.
- A replacement for RAGAS, promptfoo, or DeepEval. Those are library-shaped
  eval frameworks; Arena is the interactive, blind, ELO-driven complement.

## Success criteria

A visitor to the repo, without knowing Pete, should be able to:

1. Read the README in 60 seconds and know what the project does, who it's
   for, and why it's different from Chatbot Arena.
2. Trust the project enough to run it — signed releases, published
   `SECURITY.md`, threat model documented, CI green.
3. Point it at their own OpenAI-compatible endpoint and see a battle in
   under five minutes.
4. Codify a repeatable eval as a YAML file, commit it to their own repo,
   and run it on demand.
