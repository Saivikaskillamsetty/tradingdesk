---
name: journal
description: Review, record and close the desk's theses — open positions, watch calls, realised R and current portfolio heat. Use when the user asks what is open, what the desk is carrying, to close or resolve a call, or types /journal.
---

# The thesis journal

The record of what the desk actually said, when it said it, and what it rested
on. Its only purpose is to make calls scoreable later: an unrecorded call
cannot be graded, and ungraded calls are how a desk convinces itself it was
right all along.

Argument, if any, is a ticker, a thesis id, or a verb like `open` / `close`.
With no argument, show the open book.

## Reading the book

- `list_theses` with `status="open"` — what is live. Watch calls and positions
  both count; they are different kinds of call, not different kinds of
  importance.
- `list_theses` with a `ticker` — the history on one name, including closed
  entries. Read this before analysing a ticker the desk has looked at before.
  A second thesis that contradicts the first without acknowledging it is how
  a desk drifts.
- `get_thesis` — one call in full, with its evidence and falsifiers.

Present the open book as a compact table: id, ticker, direction, horizon, date
opened, dollar risk. Then state total risk across open positions. Do not add up
the risk column yourself — `size_position` reports portfolio heat, and if the
user wants heat as a percentage of equity, ask for the equity and get it from
there.

## Recording a thesis

Usually `/analyze` does this. Record directly when the user states a view they
want on the record.

`falsifiers` is required and is the point of the exercise. Push back on ones
that cannot be observed: "if the story changes" is not a falsifier, "below
$228.68 support" and "cash conversion below 100% for a third quarter" are. If
the user cannot name one, say the thesis is not yet scoreable and ask what
would make them abandon it.

`evidence` entries should carry the period on financial claims and the level
on technical ones, exactly as they were cited when the call was made.

Record `direction="watch"` when no position was taken. Those are the calls
most often forgotten and most often quietly re-remembered as wins.

## Closing a thesis

`close_thesis` takes an outcome, an exit price where there was one, and a note.

Outcomes: `target_hit`, `stopped_out`, `closed_manual`, `expired`,
`invalidated`. Pick the one that describes what happened, not the one that
reads best. A position closed early out of nerves is `closed_manual`, not
`invalidated`.

Realised R is computed from the recorded entry and stop — do not work it out
yourself, and do not restate it if the tool returned null because a leg was
missing.

The note is where the honest part goes: whether the thesis was right, and
whether it was right for the reason given. Those come apart more often than
anyone likes, and a call that worked for an unrelated reason is a losing
process with a winning outcome. Say so in the note; Phase 5 scoring depends
on it being there.

## Boundaries

The journal records calls. It does not open, close or size positions, and
writing a thesis is not a decision to trade. Sizing belongs to the `risk`
agent.
