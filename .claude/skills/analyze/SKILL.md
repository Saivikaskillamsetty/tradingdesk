---
name: analyze
description: Full research pass on a stock — runs fundamental and technical analysis in parallel, sizes the result against the desk's risk limits, then synthesises everything into a single verdict with cited figures. Use when the user asks to analyse, research, or look at a ticker, or types /analyze TICKER.
---

# Analyse a stock

Produces one grounded view of a company by running two specialists over the
same name, reconciling what they find, and putting any resulting trade in
front of the risk officer before it is written down.

Argument: a ticker symbol. If none was given, ask for one rather than guessing.

## Run both specialists in parallel

Dispatch **`fundamentals`** and **`chartist`** in a single message with two Task
calls, so they run concurrently. They answer different questions and neither
needs the other's output:

- `fundamentals` — is this a business worth owning?
- `chartist` — is this a reasonable moment to buy it?

Give each the ticker and any context the user supplied about their intent
(holding period, existing position, what prompted the question). Do not pass
one agent's findings to the other; independent reads are what makes their
agreement or disagreement informative.

## Bring in the others when the question demands it

Two more specialists exist. Dispatch them only when there is a reason, and say
in the output that you did.

- **`filings`** — when either specialist reports a figure they could not
  explain: a net income figure that outran operating income, a margin that
  moved sharply, a price gap with no structural cause. This is the agent that
  closes "worth checking the 10-Q" instead of leaving it in the gaps section.
  It can run in parallel with the first two when you already know the question,
  or after when the question only emerges from their reports.
- **`macro`** — when the thesis depends on the environment rather than the
  name: long-duration growth against the ten-year, overseas revenue against
  the dollar, a swing entry against an elevated VIX. Runs in parallel with the
  first two; it needs nothing from them.

Do not run either by reflex. A macro read attached to every analysis is how a
desk talks itself into positions it does not understand, and both agents cost
a round trip.

## Then the risk officer

**`risk`** runs after, not alongside — it needs the chartist's entry, stop,
target and ATR, so it cannot start until that report exists. Dispatch it only
when the reconciliation below points to an actual position. A watchlist
conclusion has nothing to size.

The risk officer needs **account equity**. If the user has not given it, ask
before dispatching. Do not supply a placeholder — a share count sized against
an invented account is worse than no share count, and it will read as real.

Pass it the ticker, direction, and the chartist's entry, stop, target and ATR
verbatim. Its verdict is binding: `vetoed` means the trade does not appear in
the output as a trade, however good the reconciliation looked. Report the veto
and what would have to change.

## Synthesise

Your job is reconciliation, not summary. Do not concatenate the reports.

**Where they agree**, say so plainly and briefly — that is the strongest signal
available.

**Where they disagree, lead with it.** Divergence is the most valuable output of
this whole process, and it usually resolves one of a few ways:

- Strong business, poor chart → often a watchlist candidate, not a buy today.
  Name the level that would change that.
- Weak business, strong chart → momentum without support. Fine for a short
  swing with a tight stop, dangerous to hold. Say which.
- Both weak → say so and stop. Do not hunt for a redeeming angle.
- Both strong → still state what would falsify the thesis.

**Never average the two into a lukewarm verdict.** "Mixed picture" with no
resolution is a non-answer; explain which read should dominate for the holding
period in question and why.

## Rules that carry through

Every figure in your output must trace back to a specialist's tool call. You
compute nothing yourself — no ratios, no averages, no reward-to-risk, and above
all no share counts. If you want a number no agent reported, ask for it rather
than deriving it.

Cite the period on financial figures (`revenue $416.2B (FY2025)`) and the level
on technical ones. If any agent flagged a gap or a limitation, it survives into
your output — do not let a clean-looking summary hide it. The risk officer's
`limitations` are checks that did not run, and an approval carrying them is
provisional; say so.

If a specialist reports a data error, say what is missing and how it narrows
the conclusion. A partial answer with a named gap is useful; a complete answer
built on a guess is not.

## Output

Keep it tight. A page, not an essay.

- **Verdict** — two or three sentences. What this is, and what you would do
  about it for the stated holding period.
- **The case for** — strongest supporting evidence, cited.
- **The case against** — strongest opposing evidence, cited. This section is
  never empty; if you cannot fill it, you have not looked hard enough.
- **If taking a position** — entry, stop and target from the chartist; shares,
  dollar risk, percentage of equity at risk and portfolio heat from the risk
  officer, with its verdict stated. If the risk officer vetoed it, this section
  says so and gives the reason. If no risk pass was run, say that rather than
  implying one.
- **What would change this view** — specific, observable triggers. Price levels,
  a filing, a metric crossing a threshold. These become the falsifiers on the
  journalled thesis, so make them observable rather than rhetorical.
- **Gaps** — data unavailable, stale, or unresolved, from any of the three.

## Journal it

End by asking whether to record the thesis. On yes, call `journal_thesis` with:

- the verdict as `thesis`, in plain language;
- `falsifiers` taken from "What would change this view" — required, and the
  reason the entry is scoreable at all;
- `evidence` as `{"claim", "source", "period"}` entries drawn from the cited
  figures, so the call can later be audited against the filings;
- `gaps` carried over verbatim;
- `direction="watch"` when the conclusion was to take no position, filling in
  entry, stop, target, shares, dollar risk and risk verdict only where there
  actually was a risk pass.

Record watch calls too. A watchlist name that ran away without you is exactly
as informative as a trade that failed, and only one of the two tends to get
remembered.

Confirm the thesis id back to the user so it can be closed later with
`close_thesis`.

## Boundaries

This is research, not advice, and not a prediction. No position is opened by
this skill. Say plainly when the answer is "nothing to do here" — that is a
successful analysis, and by volume it should be the most common one.
