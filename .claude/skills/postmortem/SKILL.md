---
name: postmortem
description: Scores the desk's closed calls and reports what to change — expectancy, win rate, whether conviction actually paid, and whether each thesis was right for the reason given. Use when the user asks how the desk has done, to review or grade a call, what the record says, or types /postmortem.
---

# Post-mortem

The calibration loop. The journal exists so calls can be graded; this is the
grading. Without it the desk has an archive of its own reasoning and no
mechanism for noticing that the reasoning is wrong, which is a more expensive
way of forgetting.

Argument, if any, is a thesis id, a ticker, or nothing. No argument reviews the
whole book.

## The question this answers

Not "did we make money". Two questions, and they come apart constantly:

1. **Was the call right?** — the outcome.
2. **Was it right for the reason given?** — the process.

A thesis that worked because of something nobody in the evidence predicted is
a losing process with a winning outcome, and it is the most dangerous result
on the board because it gets repeated. Say when that happened. The close note
is usually the only place the difference is recorded.

## Reviewing one call

`review_thesis` returns the plan and the result side by side: planned R,
realised R, R capture, holding period, how it was exited, and the falsifiers
as written.

**The falsifiers come back unchecked, and checking them is the work.** Take
each one and go and find out whether it fired:

- a price level or a moving average → dispatch **`chartist`**;
- a metric, a margin, a cash-conversion threshold → dispatch **`fundamentals`**;
- something a filing would say → dispatch **`filings`**;
- a rate, a curve, a dollar move → dispatch **`macro`**.

Dispatch them in parallel where the falsifiers are independent. If a falsifier
fired and the position was still held, that is the finding — the exit rule was
written and then not followed, and no amount of a good outcome redeems it.

**Judge the decision against what was knowable then.** `gaps_at_the_time`
lists what the desk knew it did not know when the call was made. A thesis that
failed on something disclosed only afterwards was not a bad decision; a thesis
that failed on something sitting in the last 10-Q was. Hindsight is the main
way a review produces confident nonsense.

## Reviewing the book

`score_book` returns everything numeric — expectancy in R, win rate, payoff
ratio, and the breakdowns by conviction, horizon, direction and outcome.

**Compute none of it yourself.** Not an average, not a win rate, not a total.
Quote what the tool returned. Filter it (`ticker`, `horizon`, `direction`,
`since`) rather than recalculating a subset by hand.

Read `calibration` closely; it is the part with teeth:

- **`conviction_ordering_holds`** — whether expectancy actually rose with
  stated conviction. `false` is the finding the desk least wants: conviction
  did not predict outcome, so sizing up on it was paying for a signal that is
  not there. Report it plainly and do not soften it.
- **`avg_r_capture`** — realised R as a fraction of planned R. Well below 1
  means targets sit beyond where positions actually get exited, and every
  reward:risk figure the risk officer approved was optimistic. Above 1 means
  targets are set too near and winners are being cut.
- **`avg_days_held`** against the stated `horizon` — a swing call held four
  months was re-labelled after the fact, usually by not selling.

Then pull `review_thesis` on the extremes: the worst realised R, the best, and
any call whose outcome contradicts its conviction. Patterns live in individual
calls; the aggregate only tells you where to look.

## Sample size

`score_book` reports `scored_sample` and `minimum_meaningful_sample`. Below the
minimum, **say so before any conclusion and state it as descriptive, not
predictive**. Three losing trades is not evidence of a broken process, and
telling a user to change their rules on that basis is worse than saying
nothing. A small sample does not stop you reporting the numbers; it stops you
drawing an inference from them.

## What is not in the scoreboard

`unscored` lists closed theses that produced no R, each with its reason. Two
kinds, and they are not the same:

- **Watch calls** — no position, so no R by design. Whether staying out was
  correct is not in the journal at all. Check the notable ones against what the
  name actually did: a watchlist name that ran away without you is exactly as
  informative as a trade that failed, and only one of the two gets remembered.
- **Positions closed without an exit price** — a record-keeping failure, not a
  data limitation. Name it as one. Those calls are permanently unscoreable and
  every figure in the report is computed without them.

Every entry in `limitations` survives into your output.

## Open theses

While reviewing, run `review_thesis` over anything still open and check its
falsifiers too. A falsifier that has already fired on a live position is not a
post-mortem finding, it is a live one — surface it first, before the historical
analysis, and say what it implies. Closing it belongs to `/journal`; this skill
does not close theses and does not touch the broker.

## Findings

This is the output that matters. A finding is not an observation, and it is
never a sentiment.

**Bad:** "Risk management could be tighter." "Be more patient with winners."
**Good:** "Four of six swing longs were closed manually before either level was
tested, averaging 0.4 R capture against a 2.8 R average plan. The stops were
never the binding constraint — the discomfort was."

Each finding carries: what was observed, the calls it rests on by id, and the
specific change it implies. Route the change to where it belongs:

- a limit that should move → the `risk` policy in `desk_mcp/risk.py`, which is
  code with a stated rationale, not a prompt;
- a check `/analyze` should run → the analyse skill;
- a falsifier style that keeps proving unobservable → say so, with the example.

Propose changes. Do not make them; a rule rewritten in the same pass that
discovered it has never been argued with.

If the honest finding is "nothing to change yet, the sample is too small",
that is the finding. Write it and stop.

## Output

- **Live issues first** — any open thesis whose falsifier has already fired. If
  none, one line saying so.
- **Scoreboard** — expectancy, win rate, payoff, total R, sample size. Straight
  from the tool.
- **Calibration** — conviction ordering, R capture, holding period versus
  stated horizon. State what each one means for how the desk sizes and exits.
- **Right for the wrong reason** — calls whose outcome and process disagree, in
  either direction. Usually the shortest and most useful section.
- **Findings** — as above. Two or three that matter, not eight that do not.
- **Not scored** — the unscored calls and why, watch calls included.

Keep it to a page. A review nobody rereads changes nothing.

## Boundaries

Scoring past calls is not a forecast and not advice. Nothing here opens,
closes or sizes a position, and a good scoreboard is not a reason to trade
larger — that is the risk officer's call and the limits do not move because
recent results were pleasant.
