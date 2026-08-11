# Trading Desk

Multi-agent equity research for US markets, running on Claude Code subagents
over MCP. Analysis-first: real money is never touched, and order flow goes to
an Alpaca **paper** account.

## Why this exists

Financial data is easy to fetch and easy to get wrong. US-GAAP XBRL concepts
drift per company and over time, so reading a single hardcoded concept returns
figures that are years stale with no error raised:

| Ticker | Live concept | Correct FY revenue | Naive `Revenues` lookup |
|---|---|---|---|
| AAPL | `RevenueFromContractWithCustomerExcludingAssessedTax` | $416.2B (FY25) | $62.9B — a 2018 figure |
| NVDA | `Revenues` | $215.9B (FY26) | correct, coincidentally |
| MSFT | `RevenueFromContractWithCustomerExcludingAssessedTax` | $331.8B (FY26) | $16.0B — a 2010 figure |

An agent given $62.9B for Apple writes a confident, completely wrong thesis.
The whole design follows from preventing that.

### Operating rules

1. **Agents never compute and never recall.** Every number comes from a tool
   call; ratios and indicators are calculated in Python.
2. **Every value carries provenance** — XBRL concept, period, form, accession,
   filing date — so any figure can be traced back to the filing.
3. **Stale data raises.** A number too old to answer the question is an error,
   not a footnote.
4. **Concept resolution is code with tests**, not a prompt instruction.

## Status

Complete. Research, sizing, a scoreable record, the context around a name,
paper execution that cannot be reached except through an approved thesis, and
a loop that grades the calls afterwards.

- [x] EDGAR client — rate limited, disk cached, no API key required
- [x] Concept resolver with provenance and staleness enforcement
- [x] Golden tests (23) pinned to filed 10-Ks
- [x] `desk` MCP server, verified over stdio
- [x] Phase 1 — `fundamentals` + `chartist` agents, `/analyze`
- [x] Phase 2 — `risk` (veto) + journal, `/journal`
- [x] Phase 3 — `filings`, `macro`, `screener`
- [x] Phase 4 — `pilot`, Alpaca paper execution behind risk approval
- [x] Phase 5 — `/postmortem` calibration loop
- [x] Phase 6 — `capitol`, `oracle`, `ledger`
- [x] Phase 7 — `/nimbus` orchestrator

## The agents

Ten specialists, each with a narrow remit and a matching tool allowlist. None
of them computes anything; every figure comes from a tool call.

| Agent | Answers | Explicitly does not |
|---|---|---|
| `fundamentals` | Is this a business worth owning? | Price, timing |
| `chartist` | Is this a reasonable moment? | Business quality |
| `filings` | What did the company actually say? | Compute ratios |
| `macro` | Does the environment support this? | Individual companies |
| `screener` | Which names are worth researching? | Analyse them |
| `capitol` | What are better-informed holders doing? | Business, chart, price |
| `oracle` | How far, and how likely? | **Which way** |
| `risk` | How large, and may it be taken at all? | Judge the thesis |
| `pilot` | What did the broker actually do? | Hold any view |
| `ledger` | What does the book say, and is it intact? | Hold any view |

## Orchestration

`/nimbus` is the front door. It takes any request — a ticker, a question, a
half-formed worry — routes it to the specialists that can answer it, and
reconciles what comes back into one view.

It runs in the main thread rather than as a subagent, deliberately: an agent
dispatching agents nests badly, and parallel dispatch is only available where
the conversation is. Independent specialists go out in a single message and
come back concurrently; their independence is what makes agreement between
them worth anything.

Nimbus holds no view of its own. Every claim in its output came back from a
specialist in that session, and a figure it cannot attribute to a tool call is
one it must not state. It also defers rather than rebuilds — a full research
pass on a ticker is handed to `/analyze`, not reimplemented.

| Command | Does |
|---|---|
| `/nimbus` | Routes anything to the right specialists and reconciles the answers |
| `/analyze` | Full research pass on one ticker, sized and journalled |
| `/journal` | Reads, records and closes calls |
| `/postmortem` | Grades the closed book and reports what to change |

Money moves through exactly one sequence, and the orchestrator never shortens
it:

```
research → risk approves → thesis journalled → place_order(thesis_id)
```

A request to "just buy 100 shares" has no way to be expressed — `place_order`
takes a thesis id and nothing else.

## Setup

```bash
uv sync
uv run pytest tests/golden -q     # must be green before any agent work
```

### Credentials

Keys live in the environment, never in the repo — `.mcp.json` reads them via
`${VAR}` expansion. Put them in `~/.zshrc` so they cannot be committed by
accident.

| Variable | Where to get it | Needed by |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | [app.alpaca.markets](https://app.alpaca.markets/signup) — switch the dashboard to **Paper**, then API Keys → Generate. Secret shows once. | Phase 1 (`chartist`) |
| `FRED_API_KEY` | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) — instant, free | `macro` agent. Everything else runs without it |
| `SEC_USER_AGENT` | Your own `name email` | optional; defaults in `.mcp.json` |

Paper trading needs no funding, identity check or approval — that applies only
to live accounts.

Verify without exposing anything (the script prints presence and a masked
prefix only, never a secret):

```bash
uv run python scripts/check_keys.py
```

## Data sources

| Source | Provides | Auth |
|---|---|---|
| SEC EDGAR | XBRL financials, filings, Form 4 insider, 13F institutional | none (User-Agent only) |
| Alpaca | Bars, quotes, news, paper orders/positions | free API key |
| FRED | Rates, curve, inflation | free API key |
| House Clerk | Congressional STOCK Act disclosures | none |

Deliberately **not** used: `yfinance` (unofficial endpoints, silent empty
responses) and Stooq (now behind a JS proof-of-work wall). EDGAR is preferred
for fundamentals because it is the source of record rather than a scrape of it.

## MCP tools

| Tool | Returns |
|---|---|
| `get_financials` | Full statement, 17 line items, each with provenance |
| `get_financial_history` | Historical series for one line item |
| `get_metrics` | Margins, returns, growth, leverage, cash conversion |
| `get_technicals` | Trend, momentum, volatility, support and resistance |
| `list_line_items` | Supported line item keys |
| `get_filings` | Recent filings, optionally filtered by form |
| `get_filing_text` | A filing's text as filed, markup stripped, in windows |
| `search_filing_text` | Verbatim passages around a term in a filing |
| `get_insider_activity` | Form 4 insider transactions |
| `get_macro_snapshot` | Rates, curve, inflation, jobs, vol, dollar — with changes |
| `get_macro_series` | One macro series in detail |
| `get_market_movers` / `get_most_active` | Discovery lists from the tape |
| `rank_candidates` | Orders a symbol list by relative strength |
| `size_position` | Share count, capital at risk, every limit checked, verdict |
| `get_risk_policy` | The standing limits, each with its rationale |
| `journal_thesis` | Records a call with its evidence and falsifiers |
| `list_theses` / `get_thesis` | The book, open or closed |
| `close_thesis` | Resolves a call and computes realised R |
| `get_account` | Paper account equity, cash, buying power |
| `place_order` | Sends an approved thesis to the paper broker |
| `get_broker_positions` / `get_broker_orders` | Broker state |
| `cancel_order` / `close_broker_position` | Unwind |
| `reconcile_positions` | Broker positions against journalled theses |
| `score_book` | Expectancy, win rate, payoff and calibration across closed calls |
| `review_thesis` | One call with its plan, its result and the gap between them |
| `get_institutional_holdings` | A manager's 13F book and its quarter-over-quarter changes |
| `get_congress_trades` | STOCK Act disclosures, filtered by ticker or member |
| `get_congress_activity` | Names appearing most across recent congressional filings |
| `get_price_distribution` | Expected move and terminal distribution over a horizon |
| `get_path_probabilities` | Odds of target before stop, and the breakeven win rate |
| `get_desk_health` | The desk's own record-keeping, audited for silent failures |

## Risk limits

The `risk` agent holds a veto and does not exercise judgement about it — the
limits are policy, checked in Python, and quoted back with the reason they
exist. `get_risk_policy` returns them at runtime.

| Limit | Value | Why |
|---|---|---|
| Risk per trade | 1% of equity | Ten consecutive losses cost a tenth of the account, which is survivable |
| Position size | 20% of equity | A tight stop makes a huge position look cheap; gaps do not respect stops |
| Portfolio heat | 6% of equity | The total loss accepted if every open thesis fails at once |
| Reward:risk | 2:1 minimum | Below it the strategy needs a win rate nobody here has demonstrated |
| Stop distance | 1.5x ATR(14) | A stop inside daily noise is an exit schedule, not protection |

Correlation is the one call the checks cannot make: three 1% positions on the
same driver are one 3% position in disguise, and the agent is instructed to
veto that even when every numeric limit passes.

## The journal

Theses are JSON files under `theses/`, one per call, written at the moment the
call is made — greppable, diffable, and reviewable alongside the code that
produced them. A thesis will not record without at least one falsifier, and
`direction="watch"` calls are recorded too: a watchlist name that ran away
without you is as informative as a trade that failed, and only one of the two
tends to get remembered. Realised R is computed on close from the recorded
entry and stop, so outcomes compare across positions of different sizes.

Set `DESK_THESES_DIR` to keep the book somewhere other than the repository.

## Smart money

Three populations, three different signals, and conflating them is how this
data gets misread. All of it is disclosed late; the discipline is refusing to
describe stale filings in the present tense.

| Source | Lag | What the filer knows |
|---|---|---|
| Form 4 insider | 2 business days | Legally presumed to know something |
| 13F institutional | Quarter end, filed up to 45 days later | Managed the position six weeks ago |
| Congress PTR | Up to 45 days, often late | Frequently an outside manager, not the member |

**13Fs are aggregated per security.** A manager with sub-advisers files the
same issuer on many lines — Berkshire reports Apple twelve times — so reading
the table row-wise reports a twelfth of the real position. Changes are computed
from **share counts, never values**: a holding marked down by the market is not
a sale, and treating it as one is simply wrong. Each position carries
`implied_price_per_share` as a self-check, because filings before 2023 reported
value in thousands rather than dollars.

**Congressional amounts are statutory bands.** `$1,001 - $15,000` is what was
filed; a midpoint is a number nobody reported. Transactions live inside filing
PDFs, so they are parsed from extracted text and every row carries the document
link. Scanned filings that extract to nothing are reported under
`unreadable_filings` rather than dropped — a member whose filing cannot be read
is not a member who did not trade.

## Forecasting

`oracle` produces distributions, never predictions, and the reason is worth
stating plainly.

**Drift is assumed to be zero.** Not because stocks do not rise, but because
drift cannot be estimated from the data available: the standard error of a mean
return measured from a year of daily data is roughly the annual volatility
itself, so a measured 12% drift on a 30%-vol name carries an error bar of about
±30%. Volatility is estimable from the same sample to within a few percent. So
the model forecasts the spread and refuses to forecast direction — which makes
it useless for picking sides and useful for the questions intuition gets wrong.

Two models run on every question. **Gaussian** is analytically clean and wrong
in the tails; **bootstrap** resamples the name's own history and carries its
real skew. Where they disagree, the normal assumption is doing damage, and the
gap is reported rather than averaged away.

The most decision-useful output is `edge_vs_breakeven`. A 2:1 setup must reach
target a third of the time simply to break even. When the modelled probability
sits below that, the geometry does not pay under zero drift and the entire case
has to come from the thesis — which the model cannot see and will not pretend
to.

`analytic_unlimited_time` is the closed form for a driftless walk, where the
odds depend only on the log distances to each barrier. Simulated figures sit
below it because the horizon expires; a large gap means the trade is not wrong,
it is being given too little time.

Barriers are checked at daily closes, so an intraday spike through the stop
that closes back inside is not counted — real stop-outs are somewhat more
likely than reported. Results are seeded, so the same question returns the same
answer; a probability that moved on refresh could not be quoted in a thesis or
checked afterwards.

## Desk health

`get_desk_health` audits the desk rather than a company, because the failures
that corrupt every other number here are silent ones:

| Finding | What it corrupts |
|---|---|
| Unparseable thesis file | `list_theses` skips it silently — the book is quietly short and heat is understated |
| Closed without an exit price | No realised R, ever; permanently outside every `score_book` figure |
| Open position with no dollar risk | Real exposure contributing nothing to portfolio heat |
| Open thesis past its horizon | Still consuming heat while nobody watches it |
| Missing credential | Names which tools will fail, before an agent commits to reasoning that needs them |

It runs offline and checks credentials for **presence, not validity** — a
revoked key passes here and fails at the call site. Values are never read into
a response.

## The calibration loop

`/postmortem` grades what the journal recorded. It asks two questions that are
routinely confused for one:

**Was the call right?** Expectancy in R, win rate, payoff ratio — all computed
in Python, all quoted rather than derived by an agent.

**Was it right for the reason given?** Every thesis stores falsifiers, and
`review_thesis` returns them *unchecked*. Answering them means dispatching the
chartist at the price level, or the filings agent at the disclosure, and
finding out what actually happened. A thesis that worked because of something
nobody in the evidence predicted is a losing process with a winning outcome,
and it is the result most likely to be repeated.

Three numbers do the calibration work:

| Reading | What it means when it breaks |
|---|---|
| `conviction_ordering_holds` | `false` — expectancy did not rise with stated conviction, so sizing up on conviction was paying for a signal that is not there |
| `avg_r_capture` | Realised R over planned R. Well below 1 means targets sit beyond where positions really get exited, and every approved reward:risk was optimistic |
| `avg_days_held` vs `horizon` | A swing call held four months was re-labelled after the fact, usually by not selling |

Nothing that produced no R is quietly counted as a scratch. Watch calls have no
R by design; a position closed without an exit price is a record-keeping
failure. Both appear under `unscored` with the reason, and both are excluded
from every performance figure rather than dragging it toward zero.

`minimum_meaningful_sample` is 20. Below it the skill is instructed to report
the numbers as descriptive and draw no inference — three losing trades is not
evidence of a broken process, and changing the rules on that basis is worse
than doing nothing.

Findings propose changes; they do not make them. A limit that should move is a
change to `desk_mcp/risk.py`, argued for separately — a rule rewritten in the
same pass that discovered it has never been argued with.

## Execution

Paper only. The base URL is the paper endpoint, hardcoded with no environment
variable that redirects it, and the account number is checked for Alpaca's
`PA` prefix before any order is sent — live keys against the paper URL fail
closed rather than trading.

The gate is the shape of the function rather than an instruction to an agent:

```
place_order(thesis_id)      # and nothing else
```

There is no symbol parameter, no quantity, no price. All of it is read back
out of the journal entry, which exists only because the risk officer approved
it and only carries a share count the risk officer computed. An agent asked to
buy 100 shares of something has no way to express that.

An order is refused when the thesis is closed, is a watch call, carries a
vetoed verdict, was sized at zero shares, lacks an entry or stop, or already
has an order attached — the last of which is what stops a retried call from
opening a second position in the same name.

Orders go out as brackets, so the stop the risk officer sized against is
submitted with the entry rather than left to a later call that might never
happen. `reconcile_positions` compares what the broker holds against what the
journal knows about, which is the only way to see the untracked exposure that
portfolio heat is blind to.

## Known limitations

- **Q4 quarterly gaps.** Q4 gets no standalone 10-Q; it must be derived as
  FY minus Q1–Q3. Not yet implemented — quarterly series skip Q4.
- **Alpaca free tier is IEX-only** (~2% of consolidated volume), so daily-bar
  volume is unreliable for swing setups. The price layer sits behind an
  adapter so Tiingo or another EOD source can be swapped in without touching
  agent code. Same seam later carries a non-US broker.
- **Non-US-GAAP filers** (foreign issuers on IFRS) are not covered by the
  current concept registry.
- **The screener has no universe.** It ranks a list you give it and reads the
  venue's movers and most-active tables. It cannot screen "all US software
  above $2B" — there is no fundamental universe behind it, and a themed list
  assembled by an agent is a recollection, not a screen. The `screener` agent
  is instructed to say which it is.
- **Filing text is text.** `get_filing_text` strips markup and returns what
  was filed. Tables survive as readable rows, but nothing is parsed into
  figures — a number read out of filing prose has no XBRL concept behind it,
  so prefer `get_financials` whenever the figure exists there.
- **Macro needs its own key.** Without `FRED_API_KEY` the macro tools fail
  with a message saying where to get one. Nothing else on the desk depends
  on them.
- **A submitted order is not a filled one.** A limit entry may never fill,
  and the position does not exist until it does. The `pilot` agent is
  instructed never to describe one as the other.
- **Portfolio heat only sees the journal.** A position taken without recording
  it is invisible to the risk checks, so the heat number is exposure as
  recorded rather than exposure as held. `size_position` says so in its
  `limitations` on every call.
- **13Fs are long US equity only.** No shorts, no cash, no bonds, no foreign
  listings. Portfolio weights are weights within the reported slice, so a
  manager described as "22% in Apple" is 22% of the part they had to disclose.
- **Congress coverage is House-only and partial.** The Senate publishes
  separately and is not read. Only the reports actually opened are searched, so
  an absent ticker means "not in the reports read", never "not traded" — the
  response states how many of how many were parsed.
- **Congressional trades are parsed from PDF text**, with no structured source
  behind them. The same caveat as filing text, one step weaker: follow the
  document link before quoting a specific transaction.
- **Forecasts assume volatility persists and drift is zero.** Neither holds
  across an earnings date, and the bands are wrong in both directions over one.
  The bootstrap resamples days independently, so it reproduces fat tails but
  not volatility clustering — real drawdowns arrive in consecutive sessions
  more often than the model allows.
- **`get_desk_health` is offline.** A reachable-but-broken API looks healthy,
  and a revoked credential passes a presence check.

## Not financial advice

A research tool, not a prediction engine. `/postmortem` exists because calls
need to be scored honestly rather than remembered selectively — and a good
scoreboard is not a reason to trade larger. The limits do not move because
recent results were pleasant.
