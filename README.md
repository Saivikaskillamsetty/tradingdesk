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

Phase 2 complete: research, sizing and a scoreable record.

- [x] EDGAR client — rate limited, disk cached, no API key required
- [x] Concept resolver with provenance and staleness enforcement
- [x] Golden tests (23) pinned to filed 10-Ks
- [x] `desk` MCP server, verified over stdio
- [x] Phase 1 — `fundamentals` + `chartist` agents, `/analyze`
- [x] Phase 2 — `risk` (veto) + journal, `/journal`
- [ ] Phase 3 — `filings`, `macro`, `screener`
- [ ] Phase 4 — Alpaca paper execution behind risk approval
- [ ] Phase 5 — `/postmortem` calibration loop

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
| `FRED_API_KEY` | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys) — instant, free | Phase 3 (`macro`) |
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
| SEC EDGAR | XBRL financials, filings, Form 4 insider | none (User-Agent only) |
| Alpaca | Bars, quotes, news, paper orders/positions | free API key |
| FRED | Rates, curve, inflation | free API key |

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
| `get_insider_activity` | Form 4 insider transactions |
| `size_position` | Share count, capital at risk, every limit checked, verdict |
| `get_risk_policy` | The standing limits, each with its rationale |
| `journal_thesis` | Records a call with its evidence and falsifiers |
| `list_theses` / `get_thesis` | The book, open or closed |
| `close_thesis` | Resolves a call and computes realised R |

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

## Known limitations

- **Q4 quarterly gaps.** Q4 gets no standalone 10-Q; it must be derived as
  FY minus Q1–Q3. Not yet implemented — quarterly series skip Q4.
- **Alpaca free tier is IEX-only** (~2% of consolidated volume), so daily-bar
  volume is unreliable for swing setups. The price layer sits behind an
  adapter so Tiingo or another EOD source can be swapped in without touching
  agent code. Same seam later carries a non-US broker.
- **Non-US-GAAP filers** (foreign issuers on IFRS) are not covered by the
  current concept registry.
- **Portfolio heat only sees the journal.** A position taken without recording
  it is invisible to the risk checks, so the heat number is exposure as
  recorded rather than exposure as held. `size_position` says so in its
  `limitations` on every call.

## Not financial advice

A research tool, not a prediction engine. The `/postmortem` loop exists
because calls need to be scored honestly rather than remembered selectively.
