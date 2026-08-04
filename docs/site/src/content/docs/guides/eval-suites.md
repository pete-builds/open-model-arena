---
title: Eval Suites
description: Codify prompt sets your team actually runs, then execute them as a batch — the killer capability for picking a production model with data.
---

Eval suites turn Arena from "one-off blind battles" into "reproducible eval
a team can point at any model tier." A suite is a YAML file of prompts.
Running it fires one battle per prompt, uses the [configured
judge](/guides/judge-mode/) to cast votes automatically, and records
per-model win / loss / tie tallies + total spend under a `run_id` so you
can diff runs over time.

## File format

Drop a file under `suites/` (create the directory if it doesn't exist):

```yaml
# suites/team-eval.yaml
name: team-eval
description: |
  Real prompts we ship against. Judged by GPT-4o.
category: coding
prompts:
  - id: sql-window-fn
    prompt: |
      Given a table `events(user_id, ts, event_type)`, write a SQL query
      that returns each user's third `signup` event, ordered by ts.
  - id: python-refactor
    prompt: |
      Refactor this function to be idempotent and testable:

      def process_signup(user_id):
          send_email(user_id, "welcome")
          record_metric("signup", user_id)
          return True
  - id: explain-concept
    prompt: |
      Explain "backpressure" in a streaming system to someone who
      understands HTTP but not distributed systems. One paragraph.
```

Rules the loader enforces:

- One suite per file. Filename is convention; `name:` is authoritative.
- `name` must be unique across all files.
- `prompts` must be a non-empty list.
- Every prompt needs a unique `id` (used for diff'ing runs) and a
  non-empty `prompt`.
- `category` scopes to models with that category in their config; defaults
  to `general`.

Suites are read on server start. Add a file → restart.

## Running a suite

Suites REQUIRE a configured [judge](/guides/judge-mode/) — otherwise every
prompt would stall waiting for a human vote. `POST` without a judge is a
`400` error.

```bash
export ARENA_URL=https://arena.example
export ARENA_API_TOKEN=<one of your ARENA_API_TOKENS>

# List available suites
curl -sSH "Authorization: Bearer $ARENA_API_TOKEN" \
  $ARENA_URL/api/suites | jq

# Kick off a run
RUN=$(curl -sSXPOST -H "Authorization: Bearer $ARENA_API_TOKEN" \
  $ARENA_URL/api/suites/team-eval/run | jq -r .run_id)

# Poll until done
while :; do
  DETAIL=$(curl -sSH "Authorization: Bearer $ARENA_API_TOKEN" \
    $ARENA_URL/api/suites/runs/$RUN)
  STATUS=$(echo "$DETAIL" | jq -r .status)
  echo "status: $STATUS"
  [ "$STATUS" = "completed" ] && break
  sleep 5
done

# Show the tally
echo "$DETAIL" | jq '{total_cost, tally, battles: (.battles | length)}'
```

## What "run" gives you

The `run_id` payload from `GET /api/suites/runs/{run_id}` returns:

- `status` — `running`, `completed`, or `errored`
- `battles_completed` / `battles_errored` / `battles_total`
- `total_cost` — provider + judge cost combined for the whole run
- `battles` — one row per prompt with `prompt_id`, `battle_id` (linkable
  as `/battle/<id>`), `winner`, and `error` if it failed
- `tally` — per-model `{wins, losses, ties, battles}` aggregated across
  prompts

`total_cost` is the audit-of-record spend for the run. It uses real API
usage numbers where the provider returns them, not just what pricing you
configured.

## Composing suites

- **Keep them small.** 5-20 prompts per file so a run finishes in a minute
  or two. Bigger suites can wait on the async pattern in a future release.
- **Give prompts meaningful IDs.** `sql-window-fn` diff's better than `p1`
  when you compare runs across models.
- **Version them.** Commit suite YAMLs to your own repo. They're the
  contract with your team about what "good enough" means.

## Common shape: model-selection eval

Point one suite at every candidate model, run once per candidate, diff
`total_cost` and per-model win share:

```
model X: 12 wins, 6 losses, 2 ties, $0.31 total
model Y: 8 wins, 10 losses, 2 ties, $0.04 total
```

If Y wins 40% of the time at 1/8 the cost, that's your answer.
