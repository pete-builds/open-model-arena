---
title: Open Model Arena
description: Blind, cost-aware model comparison for any OpenAI-compatible endpoint.
template: splash
hero:
  tagline: |
    Blind, cost-aware model comparison for any OpenAI-compatible endpoint.
    Self-hosted. ELO leaderboard. Eval suites. LLM-as-judge.
  actions:
    - text: Get Started
      link: /getting-started/
      icon: right-arrow
      variant: primary
    - text: View on GitHub
      link: https://github.com/pete-builds/open-model-arena
      icon: external
      variant: minimal
---

## Why this exists

Public leaderboards test their models with their prompts on their hardware.
They don't tell you how the local Mistral 7B on your Mac Mini stacks up
against the $15 / million tokens you pay a cloud provider for the prompts
your team actually ships.

Open Model Arena runs on your infrastructure, with your models, your
prompts, and your data. A `$0` local model and a `$15/million-token` cloud
API get the same blind evaluation.

## What ships

- **Blind battles** — two responses, same prompt, no names until you vote
- **ELO leaderboard** — K=32, per-category, tied ranks preserved
- **LLM-as-judge** — designate any configured model as an automated evaluator
- **Eval suites** — YAML prompt sets, batch runs, per-run tally
- **Cost dashboard** — spend by model, measured cost per 1k tokens
- **Prometheus metrics** — battles, votes, latency, cost, all scrapeable
- **Bearer-token API** — headless / CI drives everything
- **Battle permalinks** — every completed battle has a shareable URL
- **Passphrase + CSRF auth** for the browser, bearer for automation

## Not this

- Not a replacement for RAGAS / promptfoo / DeepEval — those are library-
  shaped eval frameworks; Arena is the interactive, blind, ELO-driven
  complement.
- Not multi-tenant SaaS. Single team, self-hosted, behind a passphrase.
- Not a model host — bring your own OpenAI-compatible endpoint.
