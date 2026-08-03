---
name: analyze
description: Full research pass on a stock — runs fundamental and technical analysis in parallel, then synthesises them into a single verdict with cited figures. Use when the user asks to analyse, research, or look at a ticker, or types /analyze TICKER.
---

# Analyse a stock

Produces one grounded view of a company by running two specialists over the
same name and reconciling what they find.

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

## Synthesise

Your job is reconciliation, not summary. Do not concatenate the two reports.

**Where they agree**, say so plainly and briefly — that is the strongest signal
available at this stage.

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
compute nothing yourself — no ratios, no averages, no reward-to-risk you worked
out in your head. If you want a number neither agent reported, ask for it rather
than deriving it.

Cite the period on financial figures (`revenue $416.2B (FY2025)`) and the level
on technical ones. If either agent flagged a gap or a limitation, it survives
into your output — do not let a clean-looking summary hide it.

If either specialist reports a data error, say what is missing and how it
narrows the conclusion. A partial answer with a named gap is useful; a complete
answer built on a guess is not.

## Output

Keep it tight. A page, not an essay.

- **Verdict** — two or three sentences. What this is, and what you would do
  about it for the stated holding period.
- **The case for** — strongest supporting evidence, cited.
- **The case against** — strongest opposing evidence, cited. This section is
  never empty; if you cannot fill it, you have not looked hard enough.
- **If taking a position** — entry, stop, target and reward:risk from the
  chartist. Flag that sizing is not yet covered; the risk officer arrives in
  Phase 2.
- **What would change this view** — specific, observable triggers. Price levels,
  a filing, a metric crossing a threshold.
- **Gaps** — data unavailable, stale, or unresolved.

End by asking whether to journal the thesis. Once Phase 2 lands, that is what
makes calls scoreable later — an unrecorded call cannot be graded, and
ungraded calls are how people convince themselves they were right all along.

## Boundaries

This is research, not advice, and not a prediction. No position is opened by
this skill. Say plainly when the answer is "nothing to do here" — that is a
successful analysis, and by volume it should be the most common one.
