---
title: Classroom Mode
description: Run a battle on the projector, pick the thinking level, and let every phone in the room vote.
---

Two features turn a solo arena into a room activity: the **thinking
selector** and the **audience vote**. Neither needs configuration.

## Thinking selector

The toolbar under the prompt has a `thinking:` dropdown: off, low,
medium, high. The value is sent to both models as `reasoning_effort`
and recorded on the battle, the export, and the reveal.

- Models that stream a reasoning trace (`reasoning_content`) get a
  collapsible **thinking** block above the answer. It opens while the
  model thinks and collapses once the answer arrives, with a character
  and reasoning-token count in the summary.
- A model that rejects the parameter with a 400 is retried once without
  it. The panel header says "thinking not supported here, answering
  plainly" and the battle proceeds. The reveal shows the effort that was
  actually applied, which is `off` for that side.
- To skip the retry dance, pin the behavior per model in `models.yaml`:

```yaml
  - id: deepseek-r1
    reasoning: true     # supports reasoning_effort; a 400 is a real error
  - id: llama3-8b
    reasoning: false    # never send reasoning_effort
```

Leaving `reasoning` out means auto, which is right for most gateways.

## Pick one model, draw the other

The two model selectors are independent now. Leave both on "mystery
match" for a blind draw, set both for a head-to-head, or set one and let
the arena pick its opponent from the same category. The model you chose
keeps the slot you put it in.

## Audience vote

1. Run a battle and wait for both answers to finish.
2. Under the vote buttons, click **let the audience vote (phones)**.
3. A panel opens with a QR code, the join URL, and a six-character code.
   Put it on the projector.
4. Students open the link on their phones. They see the prompt, both
   responses, and three buttons. Each phone gets one vote, changeable
   until the poll closes. The running count is visible; the split is
   not, so early votes cannot pull later ones.
5. Watch the live tally bars on your screen, then click **CLOSE POLL &
   REVEAL**. The plurality becomes the battle's recorded vote with
   `method = "audience"`, ELO updates as usual, and every phone flips to
   the reveal: which model was A, which was B, the tally, and whether
   their pick matched the room.

You can still vote by hand or hand the battle to the judge while a poll
is open. Either one wins the race: closing the poll afterward returns
`409` and the poll is simply abandoned.

### What the phones can and cannot do

The audience surface (`/vote/<code>` and `/api/audience/...`) sits
outside the passphrase gate on purpose. It can read the two finished
responses for its poll and record one choice per device. It cannot
create battles, reach a model provider, read the leaderboard, or see
model names before the poll closes.

Guardrails: codes come from an unambiguous 31-character alphabet, polls
expire six hours after opening, each poll caps at 1,000 distinct voters,
and the vote endpoint is limited to 300 requests per minute per IP. That
last number is deliberately loose: a whole class usually arrives from one
NAT address or one tunnel, and the real cap is per-poll voters.

### Remote classrooms

The join URL is whatever origin the presenter's browser is on. If the
arena is only reachable on a LAN, phones must be on that LAN. Put it
behind a tunnel (see [Deployment](/guides/deployment/)) and the QR code
works from anywhere.
