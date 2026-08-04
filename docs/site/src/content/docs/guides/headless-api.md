---
title: Headless / CI API
description: Drive Arena from a shell, a cron job, or a CI runner using bearer tokens.
---

Every `/api/*` route accepts a bearer token in addition to the browser's
cookie session. That means CI can create battles, kick off eval suites,
scrape metrics, and read cost breakdowns without ever running a login
flow.

## Configure

Generate one or more tokens (comma-separated) and add to `.env`:

```bash
echo "ARENA_API_TOKENS=$(openssl rand -hex 32)" >> .env
```

Restart the app. That's it.

## Auth headers

Send either standard `Authorization` or the `X-API-Token` alias:

```bash
# Standard
curl -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/models

# Alias — useful if a proxy or CI system already claims Authorization
curl -H "X-API-Token: $ARENA_API_TOKEN" \
  https://arena.example/api/models
```

Bearer requests skip CSRF (bearer tokens aren't carried on cross-site
navigations, so CSRF isn't applicable — see [Threat Model](/reference/threat-model/)).

## Common recipes

### Run a battle from a script

```bash
BATTLE=$(curl -sS -H "Authorization: Bearer $ARENA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Explain closures","category":"coding"}' \
  https://arena.example/api/battle | jq -r .battle_id)

# Stream the response as SSE
curl -N -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/battle/$BATTLE/stream

# Or just wait for it to finish, then let the judge vote
sleep 30
curl -sSXPOST -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/battle/$BATTLE/judge | jq
```

### Kick off an eval suite nightly

Cron entry:

```
0 3 * * * /usr/local/bin/run-arena-suite.sh production-eval
```

```bash
#!/usr/bin/env bash
# run-arena-suite.sh
set -euo pipefail
: "${ARENA_URL:?}"
: "${ARENA_API_TOKEN:?}"
SUITE=${1:?suite name required}

RUN=$(curl -sSfXPOST -H "Authorization: Bearer $ARENA_API_TOKEN" \
  "$ARENA_URL/api/suites/$SUITE/run" | jq -r .run_id)

echo "Started $SUITE as $RUN, polling..."
while :; do
  STATE=$(curl -sSfH "Authorization: Bearer $ARENA_API_TOKEN" \
    "$ARENA_URL/api/suites/runs/$RUN")
  STATUS=$(echo "$STATE" | jq -r .status)
  if [ "$STATUS" = "completed" ]; then
    echo "$STATE" | jq '{total_cost, tally, battles: (.battles | length)}'
    exit 0
  fi
  if [ "$STATUS" = "errored" ]; then
    echo "$STATE" | jq
    exit 1
  fi
  sleep 5
done
```

### Scrape Prometheus

```yaml
# prometheus.yml
scrape_configs:
  - job_name: model-arena
    metrics_path: /api/metrics
    scrape_interval: 30s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/arena-token
    static_configs:
      - targets: ['arena.internal:3694']
```

`credentials_file` should be a single-line file containing the token, mode
`600`. See [Metrics](/reference/metrics/) for the counter list.

### Pull the cost breakdown into a spreadsheet

```bash
curl -sSH "Authorization: Bearer $ARENA_API_TOKEN" \
  "https://arena.example/api/costs?days=30" \
  | jq -r '.per_model | (["model","cost","tokens","battles","share%","cost_per_1k_out"] | @csv), (.[] | [.display_name, .total_cost, .total_output_tokens, .battles, .share_pct, .measured_cost_per_1k_output_tokens] | @csv)' \
  > arena-costs.csv
```

## Multi-token / rotation

`ARENA_API_TOKENS` is a comma-separated list. Rotate by adding the new
token first, updating clients, then removing the old token. The comparison
is constant-time against every token — no position leak.

## What bearer tokens can NOT do

- Log in as a cookie session. Bearer stays bearer.
- Bypass rate limits. `/api/battle` is still capped at 10 requests per
  minute per client IP.
- Retrieve provider API keys. Keys are read from env vars only and never
  echoed back from any endpoint.
