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

Phase 0 complete: data foundation and correctness gate.

- [x] EDGAR client — rate limited, disk cached, no API key required
- [x] Concept resolver with provenance and staleness enforcement
- [x] Golden tests (23) pinned to filed 10-Ks
- [x] `desk` MCP server, verified over stdio
- [ ] Phase 1 — `fundamentals` + `chartist` agents, `/analyze`
- [ ] Phase 2 — `risk` (veto) + journal
- [ ] Phase 3 — `filings`, `macro`, `screener`
- [ ] Phase 4 — Alpaca paper execution behind risk approval
- [ ] Phase 5 — `/postmortem` calibration loop

## Setup

```bash
uv sync
uv run pytest tests/golden -q     # must be green before any agent work
```

Alpaca paper trading (free) needs keys exported before launching Claude Code —
`.mcp.json` reads them from the environment so no secret enters the repo:

```bash
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...
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
| `list_line_items` | Supported line item keys |
| `get_filings` | Recent filings, optionally filtered by form |
| `get_insider_activity` | Form 4 insider transactions |

## Known limitations

- **Q4 quarterly gaps.** Q4 gets no standalone 10-Q; it must be derived as
  FY minus Q1–Q3. Not yet implemented — quarterly series skip Q4.
- **Alpaca free tier is IEX-only** (~2% of consolidated volume), so daily-bar
  volume is unreliable for swing setups. The price layer sits behind an
  adapter so Tiingo or another EOD source can be swapped in without touching
  agent code. Same seam later carries a non-US broker.
- **Non-US-GAAP filers** (foreign issuers on IFRS) are not covered by the
  current concept registry.

## Not financial advice

A research tool, not a prediction engine. The `/postmortem` loop exists
because calls need to be scored honestly rather than remembered selectively.
