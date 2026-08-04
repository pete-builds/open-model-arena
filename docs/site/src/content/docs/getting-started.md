---
title: Getting Started
description: Point Open Model Arena at any OpenAI-compatible endpoint and run your first battle in under five minutes.
---

You need Docker and at least two OpenAI-compatible endpoints. If you're
running Ollama locally, one endpoint is already covered.

## 1. Clone + config

```bash
git clone https://github.com/pete-builds/open-model-arena.git
cd open-model-arena
cp models.yaml.example models.yaml
cp .env.example .env
```

Generate an auth secret:

```bash
echo "AUTH_TOKEN_SECRET=$(openssl rand -hex 32)" >> .env
```

Set your passphrase in `.env`:

```env
ARENA_PASSPHRASE=your-shared-passphrase
```

## 2. Wire your models

Edit `models.yaml`. The example ships with GPT-4o + GPT-4o Mini via OpenAI
and Llama 3 + Mistral via Ollama. Delete what you don't have; add what you
do. Every entry needs a `provider` (already declared) and a `model_id` the
provider recognizes.

```yaml
providers:
  ollama:
    base_url: "http://localhost:11434/v1"
    api_key: "ollama"
    local: true

models:
  - id: llama3-8b
    provider: ollama
    display_name: "Llama 3 8B (Local)"
    model_id: "llama3:8b"
    input_cost_per_1m: 0.0
    output_cost_per_1m: 0.0
    categories: [general, coding, reasoning]
    enabled: true
```

Provider API keys read from environment variables via `api_key_env` — see
[Deployment](/guides/deployment/) for the full pattern.

## 3. Boot

```bash
docker compose up -d
```

Open <http://localhost:3694>, enter your passphrase, and run a battle.

Two things to try next:

- **[LLM-as-judge](/guides/judge-mode/)** — add a `judge:` block to
  `models.yaml` and get an automated vote button.
- **[Eval suites](/guides/eval-suites/)** — drop a YAML file in `suites/`
  and fire it from the API.

## Remote access

Auth cookies use `Secure=True`, which requires HTTPS. On `localhost` this
is automatic; for remote deploys, terminate TLS at any reverse proxy —
Caddy, nginx, Cloudflare Tunnel, Tailscale Funnel — and route to
`http://arena:3694`. See [Deployment](/guides/deployment/).
