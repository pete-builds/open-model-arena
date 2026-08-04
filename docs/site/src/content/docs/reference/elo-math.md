---
title: ELO Math
description: The exact rating formula Arena uses, the concurrency fix that keeps it honest, and how provisional ranks work.
---

Arena uses the classical chess ELO formula, unmodified, with K = 32. Two
things beyond the vanilla implementation worth calling out: how tied ranks
are handled, and the atomic-claim pattern that prevents double-counting
under concurrent votes.

## The formula

For a battle between models A and B with pre-vote ratings `r_a` and
`r_b`:

```
expected_a = 1 / (1 + 10 ** ((r_b - r_a) / 400))
expected_b = 1 - expected_a
```

Score depending on outcome:

| Winner | s_a | s_b |
|---|---|---|
| `a` | 1.0 | 0.0 |
| `b` | 0.0 | 1.0 |
| `tie` | 0.5 | 0.5 |

Post-vote ratings:

```
r_a' = r_a + K * (s_a - expected_a)
r_b' = r_b + K * (s_b - expected_b)
```

`K = 32` (the standard "new-player" chess K-factor). Higher K = ratings
move faster per battle, at the cost of more noise from any single vote.

## Per-category + overall

Every vote updates two rating rows for each model: the `overall` category,
and the specific category the battle was tagged with (e.g. `coding`,
`reasoning`). The leaderboard endpoint accepts a `?category=<name>` query
to switch between them.

Starting rating for a new (model, category) pair is `1500.0`.

## Provisional ranks

A model with fewer than **5** battles in a category is shown as
"provisional" and never assigned a rank number. It appears at the bottom
of the leaderboard sorted by rating. This bound is deliberately low; the
point is to keep a first-hour deployment usable while acknowledging that
ELO after 2 votes means very little.

Configurable in `app/main.py` via `MIN_BATTLES_FOR_RANKING = 5`.

## Tied ranks

If two ranked models have identical rounded ratings, they share the same
rank number:

```
1  Model X   1610.4
1  Model Y   1610.4
3  Model Z   1583.2
```

Not "1, 2, 3" with an arbitrary tiebreak. If the numbers say two models
are equal, the display should too.

## Identical response detection

If both models return the exact same text (including whitespace-stripped
comparison), only the TIE button is enabled. There's no meaningful signal
in "which of these identical strings do you prefer."

## The concurrency fix

`record_vote()` used to read `battle.winner`, check it was `NULL`, then
write the winner and the ELO updates many `await` boundaries later. Under
concurrent `POST .../vote` for the same battle, both requests could pass
the check, both would write, and ELO ended up double-counted. The
`vote_log` table ended up with two rows.

Since v0.1.0 the pattern is an atomic conditional claim:

```sql
UPDATE battles
   SET winner = ?, voted_at = datetime('now')
 WHERE id = ? AND winner IS NULL
```

If `rowcount == 0`, we raise `ValueError("already voted")`. Only the first
caller wins the claim; the ELO update and vote_log insert then run
exactly once per battle, committed together.

A regression test in `tests/test_store.py` fires two concurrent
`record_vote` calls via `asyncio.gather` and asserts exactly one succeeds,
`vote_log` has one row, and ratings moved once.

## What "K = 32" means for you

- A model that beats a 1500-rated model when both are 1500-rated gains
  16 ELO.
- A model that beats a 200-point-higher-rated model gains 24 ELO (the
  upset bonus).
- A model that loses to a 200-point-lower-rated model loses 24 ELO
  (the upset penalty).

If you want less-noisy rankings at the cost of slower convergence, drop
K in `app/store.py`. If you want faster convergence, raise it. The whole
audit trail is in `vote_log`, so recomputing from scratch under a
different K is a trivial script.
