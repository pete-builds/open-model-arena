# Eval Suites

Codified prompt sets that mirror the work your team actually does. Drop a
YAML file in this directory; the server picks it up on startup and exposes it
under `/api/suites`.

## Format

```yaml
name: my-team-eval
description: |
  The prompts we ship real work against.
category: coding                   # optional; scopes to models with this category
prompts:
  - id: sql-window-fn
    prompt: |
      Given a table `events(user_id, ts, event_type)`, write a SQL query…
  - id: python-refactor
    prompt: |
      Refactor this function to be idempotent and testable:
      ...
```

## Running a suite

Suites require a judge model (`judge:` section in `models.yaml`), because
batch runs use the judge to cast votes automatically — otherwise every prompt
would stall on a human vote.

```bash
# List available suites
curl -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/suites

# Kick off a run
curl -X POST -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/suites/my-team-eval/run

# Fetch the run's per-model win tally
curl -H "Authorization: Bearer $ARENA_API_TOKEN" \
  https://arena.example/api/suites/runs/{run_id}
```

## Convention

- One suite per file, filename is the suite `name` with `.yaml` extension.
- Prompt IDs must be unique within a suite; they let you diff results across
  runs.
- Suites are read on server start; add a new file and restart to pick it up.
