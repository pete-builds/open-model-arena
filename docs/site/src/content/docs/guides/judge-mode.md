---
title: LLM-as-Judge
description: Designate any configured model as an automated evaluator that casts votes against a rubric — human vote always still available.
---

Judge mode adds an automated evaluator. When enabled, the battle view
shows a **let \<model\> decide** button. Clicking it sends both responses
to the judge, which scores them against a rubric and casts the vote. The
judge's reasoning is recorded on the vote log and surfaced in the reveal
view.

Judge mode is what makes [Eval Suites](/guides/eval-suites/) work — batch
runs use the judge to cast every vote, so you get a full per-model tally
without a human in the loop.

## Enable

Add a `judge:` block to `models.yaml`:

```yaml
judge:
  model: gpt-4o           # must match an id in the models list above
  # rubric: |             # optional; a sensible default is used when omitted
  #   Score correctness first, then helpfulness, then clarity.
```

The judge must be a model that also appears in your `models:` list. Any
OpenAI-compatible endpoint works — it can be one of your competing models
or a separate one dedicated to judging.

## Rubric

The default rubric asks the judge to:

- Score correctness, faithfulness to the prompt, helpfulness, clarity
- Penalize hallucinations and evasive answers
- Return a compact JSON object: `{"winner": "a"|"b"|"tie", "reasoning": "..."}`
- Treat errored / empty responses as auto-loss

Override with your own rubric if the default doesn't match your team's
priorities. The rubric is sent as the judge's system message.

## Anti-bias notes

- Judge output text is capped at 8k chars per response side before the
  judge sees it — a runaway model can't blow the judge's context window.
- Temperature is fixed at `0.0` for stable, reproducible verdicts across
  re-runs.
- Model identities are not revealed to the judge; it sees A/B labels only.
  A same-family judge (say, Claude Sonnet judging Claude Opus vs. GPT-4o)
  can still self-favor by style, but it can't self-favor by name.
- The JSON parser tolerates fenced (```json ... ```), prose-wrapped, and
  plain replies — most models comply.

## What gets recorded

Every judge vote lands in the vote log with:

- `method = "judge"`
- `judge_reasoning` — the one-to-two-sentence explanation
- `judge_model_id` — which judge cast the vote
- `judge_cost` — dollar cost of the judge call, separate from battle cost

`GET /api/battle/{id}` returns all of these; the reveal view shows the
reasoning inline. The [Cost Dashboard](/reference/api/#costs) folds judge
cost onto the judge model.

## When to override

If the judge picks the winner but you disagree, you can always cast a
human vote — but only if the judge failed (e.g. the endpoint 502'd). Once
a vote lands, the battle is closed. Design intent: audit-of-record trumps
after-the-fact override.

## Cost

Judge mode adds one API call per vote. For eval suites that's one extra
call per prompt. Judge cost is tracked separately in `judge_cost` and
Prometheus (`arena_judge_cost_dollars_total`), so you can see exactly what
the automation costs you.

## Failure modes

The endpoint surfaces judge failures explicitly:

- `400` if judge isn't configured or responses are missing
- `409` if the battle already has a vote
- `502` if the judge call itself failed (network, timeout, bad JSON)

For a `502`, fall back to a human vote.
