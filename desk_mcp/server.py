"""MCP server backing the trading desk's research agents.

Every tool here returns values with provenance attached. Agents are instructed
never to state a figure they did not receive from one of these calls, and to
cite the period it covers -- see .claude/agents/*.md.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from desk_mcp import (
    execution,
    forecast,
    health,
    journal,
    macro,
    metrics,
    postmortem,
    risk,
    screener,
    smartmoney,
    technicals,
)
from desk_mcp.edgar import documents, facts, filings
from desk_mcp.edgar.client import EdgarError
from desk_mcp.edgar.concepts import ALL_KEYS
from desk_mcp.execution import ExecutionError
from desk_mcp.forecast import ForecastError
from desk_mcp.journal import JournalError
from desk_mcp.macro import MacroError
from desk_mcp.postmortem import PostmortemError
from desk_mcp.prices.source import PriceError
from desk_mcp.risk import RiskError
from desk_mcp.screener import ScreenerError
from desk_mcp.smartmoney import SmartMoneyError

mcp = MCPServer(
    name="desk",
    instructions=(
        "Authoritative financial data sourced from SEC EDGAR filings. "
        "Every value carries the XBRL concept, reporting period and filing "
        "accession it came from. State no figure you did not receive from "
        "one of these tools, and cite the period it covers. If a tool "
        "returns an `error` key, report the gap rather than substituting a "
        "remembered or estimated number."
    ),
)


def _error(exc: Exception) -> dict[str, Any]:
    """Surface failures as data so an agent can react instead of guessing."""
    return {
        "error": type(exc).__name__,
        "message": str(exc),
        "guidance": (
            "Do not substitute a remembered or estimated figure. "
            "Report the gap to the user instead."
        ),
    }


@mcp.tool()
def get_financials(ticker: str, period: str = "annual") -> dict[str, Any]:
    """Latest full financial statement for a company, from SEC XBRL filings.

    Returns every available line item (revenue, margins, cash flow, balance
    sheet) with the concept, period and filing each value came from. Line
    items a company does not report appear under `unavailable` rather than
    causing an error.

    Args:
        ticker: Stock symbol, e.g. "AAPL".
        period: "annual" or "quarterly".
    """
    try:
        return facts.statement(ticker, period=period)
    except (EdgarError, ValueError, KeyError) as exc:
        return _error(exc)


@mcp.tool()
def get_financial_history(
    ticker: str, line_item: str, period: str = "annual", limit: int = 10
) -> dict[str, Any]:
    """Historical series for one line item, oldest to newest.

    Use this for growth rates and trends. Each point states its own fiscal
    year and source filing, so a restated figure is visible as such.

    Args:
        ticker: Stock symbol, e.g. "NVDA".
        line_item: One of the supported keys, e.g. "revenue", "net_income".
        period: "annual" or "quarterly".
        limit: Maximum number of periods to return.
    """
    try:
        series = facts.series(ticker, line_item, period=period, limit=limit)
        return {
            "ticker": ticker.upper(),
            "line_item": line_item,
            "period": period,
            "points": [f.to_dict() for f in series],
        }
    except (EdgarError, ValueError, KeyError) as exc:
        return _error(exc)


@mcp.tool()
def get_metrics(ticker: str, history_years: int = 5) -> dict[str, Any]:
    """Derived financial metrics for a company: margins, returns, growth, leverage.

    Computed from SEC filings rather than estimated. Covers gross/operating/net
    margin, R&D intensity, free cash flow and FCF margin, cash conversion,
    debt/equity, net debt, ROE and ROA (on average balances where available),
    and YoY plus CAGR growth for revenue, net income and operating cash flow.

    Each metric lists the line items it was computed from and the period it
    covers. Prefer this over deriving ratios yourself from `get_financials`.

    Args:
        ticker: Stock symbol, e.g. "AAPL".
        history_years: Periods to span when computing growth rates.
    """
    try:
        return metrics.analyse(ticker, history_years=history_years)
    except (EdgarError, ValueError, KeyError) as exc:
        return _error(exc)


@mcp.tool()
def get_technicals(
    ticker: str, benchmark: str = "SPY", lookback_days: int = 500
) -> dict[str, Any]:
    """Technical picture for a symbol: trend, momentum, volatility, key levels.

    Computed from daily bars on the consolidated SIP tape, so volume is the
    full market rather than a single venue. Returns moving-average structure,
    RSI, ATR (with stop-distance guidance), relative strength versus a
    benchmark, drawdown from the one-year high, and clustered support and
    resistance annotated with what each level does at the current price.

    Anything that could not be computed from the available history is null and
    explained under `limitations` rather than approximated.

    Args:
        ticker: Stock symbol, e.g. "NVDA".
        benchmark: Symbol for relative strength, default "SPY".
        lookback_days: Calendar days of history to pull. 500 gives a 200-day
            moving average enough trading sessions.
    """
    try:
        return technicals.analyse(
            ticker, benchmark=benchmark, lookback_days=lookback_days
        )
    except (PriceError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def list_line_items() -> dict[str, Any]:
    """Financial line items this server can resolve."""
    return {"line_items": list(ALL_KEYS)}


@mcp.tool()
def get_filings(
    ticker: str, forms: list[str] | None = None, limit: int = 20
) -> dict[str, Any]:
    """Recent SEC filings for a company, newest first.

    Args:
        ticker: Stock symbol.
        forms: Optional filter, e.g. ["10-K", "8-K"]. Omit for all forms.
        limit: Maximum filings to return.
    """
    try:
        return {
            "ticker": ticker.upper(),
            "filings": filings.recent_filings(ticker, forms=forms, limit=limit),
        }
    except EdgarError as exc:
        return _error(exc)


@mcp.tool()
def get_insider_activity(ticker: str, limit: int = 25) -> dict[str, Any]:
    """Recent Form 4 insider transaction filings for a company.

    Args:
        ticker: Stock symbol.
        limit: Maximum Form 4 filings to return.
    """
    try:
        return filings.insider_activity(ticker, limit=limit)
    except EdgarError as exc:
        return _error(exc)


@mcp.tool()
def get_filing_text(
    ticker: str,
    accession: str | None = None,
    form: str | None = None,
    offset: int = 0,
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """The text of a filing as filed, markup stripped, in windows.

    Use this when the numbers raise a question they cannot answer — a margin
    that moved, a quarter that beat and sold off anyway, a net income figure
    that outran operating income. `get_filings` gives you the date; this gives
    you what the company actually said.

    Large filings are returned in windows. `truncated` and `next_offset` tell
    you whether there is more; a silently cut filing reads like a complete one.
    Prefer `search_filing_text` when you know what you are looking for.

    Args:
        ticker: Stock symbol.
        accession: Specific filing accession number. Takes precedence.
        form: Read the newest filing of this form instead, e.g. "10-Q".
        offset: Character offset to start from, for paging.
        max_chars: Characters to return, capped at 100,000.
    """
    try:
        return documents.document_text(
            ticker, accession=accession, form=form, offset=offset, max_chars=max_chars
        )
    except EdgarError as exc:
        return _error(exc)


@mcp.tool()
def search_filing_text(
    ticker: str,
    query: str,
    accession: str | None = None,
    form: str | None = None,
    context: int = 800,
    max_hits: int = 5,
) -> dict[str, Any]:
    """Passages of a filing around every occurrence of a term, verbatim.

    Far cheaper than paging a 10-K to find the one paragraph that explains a
    tax benefit or a goodwill charge. No match means the term is absent from
    this document, not that the fact is absent from the company's filings.

    Args:
        ticker: Stock symbol.
        query: Term to find, e.g. "stock-based compensation".
        accession: Specific filing accession number. Takes precedence.
        form: Search the newest filing of this form instead, e.g. "10-K".
        context: Characters of surrounding text per hit.
        max_hits: Maximum passages to return.
    """
    try:
        return documents.search_filing(
            ticker,
            query=query,
            accession=accession,
            form=form,
            context=context,
            max_hits=max_hits,
        )
    except EdgarError as exc:
        return _error(exc)


@mcp.tool()
def get_macro_snapshot(series: list[str] | None = None) -> dict[str, Any]:
    """Current macro conditions from FRED: rates, curve, inflation, jobs, vol.

    Every reading carries its FRED series id, observation date and 1-, 3- and
    12-month changes, computed against the last real print rather than a
    calendar date. Series that fail are listed under `unavailable` rather than
    taking the whole snapshot down.

    Rates and inflation recalled from memory are wrong by whole percentage
    points. Always read them here.

    Args:
        series: Optional subset, e.g. ["treasury_10y", "curve_10y_2y"]. Omit
            for all of them.
    """
    try:
        return macro.snapshot(keys=series)
    except MacroError as exc:
        return _error(exc)


@mcp.tool()
def get_macro_series(key: str) -> dict[str, Any]:
    """One macro series in detail, with its 1-, 3- and 12-month changes.

    Args:
        key: Series key, e.g. "treasury_10y", "core_cpi", "vix". Call
            `get_macro_snapshot` to see them all.
    """
    try:
        return macro.series_reading(key)
    except MacroError as exc:
        return _error(exc)


@mcp.tool()
def get_market_movers(top: int = 10) -> dict[str, Any]:
    """Today's largest percentage gainers and losers.

    A starting list, not a signal — most large single-day moves are news the
    market has already priced.

    Args:
        top: How many of each to return, capped at 50.
    """
    try:
        return screener.movers(top=top)
    except (ScreenerError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def get_most_active(by: str = "volume", top: int = 10) -> dict[str, Any]:
    """The day's most heavily traded names.

    Args:
        by: "volume" or "trades".
        top: How many to return, capped at 50.
    """
    try:
        return screener.most_active(by=by, top=top)
    except (ScreenerError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def rank_candidates(
    symbols: list[str], benchmark: str = "SPY", lookback_days: int = 400
) -> dict[str, Any]:
    """Order a candidate list by relative strength, with supporting measures.

    Turns a raw list into a shortlist worth spending the research agents on.
    Returns trend structure, distance from the 50-day, RSI, ATR as a percentage
    of price and drawdown from the one-year high for each name. Symbols whose
    history could not be retrieved appear under `unavailable` rather than
    quietly dropping out.

    This orders candidates; it does not judge them.

    Args:
        symbols: Up to 40 symbols. Each costs a separate history request.
        benchmark: Symbol for relative strength, default "SPY".
        lookback_days: Calendar days of history per symbol.
    """
    try:
        return screener.rank(
            symbols, benchmark=benchmark, lookback_days=lookback_days
        )
    except (ScreenerError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def size_position(
    ticker: str,
    direction: str,
    entry: float,
    stop: float,
    account_equity: float,
    target: float | None = None,
    atr: float | None = None,
    risk_pct: float | None = None,
) -> dict[str, Any]:
    """Size a trade against the desk's risk limits and rule on whether it passes.

    Returns the share count, the capital genuinely at risk, current portfolio
    heat from open journalled theses, and every limit checked with its observed
    value and threshold. The `verdict` is one of `approved`,
    `approved_with_warnings` or `vetoed`; a veto is a refusal, not a preference.

    Never work a share count out yourself — this is where a correct thesis
    loses money. Anything that could not be checked, such as a missing ATR or
    target, appears under `limitations` rather than being assumed to pass.

    Args:
        ticker: Stock symbol, e.g. "NVDA".
        direction: "long" or "short".
        entry: Intended entry price.
        stop: Stop price. Must sit below entry for a long, above for a short.
        account_equity: Total account equity the risk budget is drawn from.
        target: Intended exit. Omit only if the chartist gave none.
        atr: ATR(14) from `get_technicals`, to check the stop against noise.
        risk_pct: Requested % of equity at risk. Clamped to the policy maximum.
    """
    try:
        return risk.size_position(
            ticker,
            direction=direction,
            entry=entry,
            stop=stop,
            account_equity=account_equity,
            target=target,
            atr=atr,
            risk_pct=risk_pct,
        )
    except (RiskError, JournalError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def get_risk_policy() -> dict[str, Any]:
    """The desk's standing risk limits, each with the reasoning behind it.

    Quote these when explaining a veto, rather than paraphrasing them.
    """
    return risk.policy()


@mcp.tool()
def journal_thesis(
    ticker: str,
    thesis: str,
    falsifiers: list[str],
    direction: str = "watch",
    horizon: str = "swing",
    conviction: str = "medium",
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    reward_risk: float | None = None,
    shares: int | None = None,
    dollar_risk: float | None = None,
    risk_verdict: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    gaps: list[str] | None = None,
) -> dict[str, Any]:
    """Record a call so it can be scored later, and return the stored thesis.

    Write this at the moment the call is made. Evidence and falsifiers cannot
    be reconstructed honestly once the outcome is known, which is the whole
    reason the journal exists.

    Record `direction="watch"` when the conclusion was to take no position —
    a watch call that would have worked is as informative as a trade that did.

    Args:
        ticker: Stock symbol.
        thesis: The call, in plain language. What this is and what to do.
        falsifiers: Observations that would prove the thesis wrong — price
            levels, a filing, a metric crossing a threshold. Required.
        direction: "long", "short" or "watch".
        horizon: "swing", "positional" or "long_term".
        conviction: Free text, e.g. "low", "medium", "high".
        entry: Entry price. Required for a long or short.
        stop: Stop price. Required for a long or short.
        target: Target price.
        reward_risk: Reward:risk, as reported by `size_position`.
        shares: Share count, as sized by `size_position`.
        dollar_risk: Capital at risk, as sized by `size_position`.
        risk_verdict: The `verdict` returned by `size_position`.
        evidence: Supporting figures, each ideally {"claim", "source",
            "period"}, so the thesis can be audited against the filings.
        gaps: Data that was unavailable, stale or unresolved at the time.
    """
    try:
        return journal.record(
            ticker,
            thesis=thesis,
            direction=direction,
            horizon=horizon,
            conviction=conviction,
            entry=entry,
            stop=stop,
            target=target,
            reward_risk=reward_risk,
            shares=shares,
            dollar_risk=dollar_risk,
            risk_verdict=risk_verdict,
            evidence=evidence,
            falsifiers=falsifiers,
            gaps=gaps,
        )
    except (JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def list_theses(
    ticker: str | None = None, status: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Recorded theses, newest first.

    Args:
        ticker: Optional symbol filter.
        status: Optional "open" or "closed" filter.
        limit: Maximum theses to return.
    """
    try:
        return {"theses": journal.search(ticker=ticker, status=status, limit=limit)}
    except (JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def get_thesis(thesis_id: str) -> dict[str, Any]:
    """One recorded thesis in full, including its evidence and falsifiers.

    Args:
        thesis_id: Identifier returned by `journal_thesis`.
    """
    try:
        return journal.load(thesis_id)
    except (JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def close_thesis(
    thesis_id: str,
    outcome: str,
    exit_price: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Close a thesis, recording how it actually resolved.

    Realised R is computed from the recorded entry and stop, so the result is
    comparable across positions of different sizes.

    Args:
        thesis_id: Identifier returned by `journal_thesis`.
        outcome: One of "target_hit", "stopped_out", "closed_manual",
            "expired", "invalidated".
        exit_price: Fill price, where there was one.
        note: What actually happened, especially if the thesis was right for
            the wrong reason.
    """
    try:
        return journal.close(
            thesis_id, outcome=outcome, exit_price=exit_price, note=note
        )
    except (JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def get_account() -> dict[str, Any]:
    """Paper account state: equity, cash, buying power, positions value.

    Refuses to return anything for an account it cannot prove is a paper
    account. Use the `equity` here as the input to `size_position` rather than
    assuming a figure.
    """
    try:
        return execution.account()
    except (ExecutionError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def place_order(thesis_id: str) -> dict[str, Any]:
    """Send a journalled, risk-approved thesis to the paper broker.

    Takes a thesis id and nothing else, by design. Symbol, share count, entry,
    stop and target all come from the journal entry, which exists only because
    the risk officer approved it. There is no way to place an order this desk
    did not size.

    Refuses when: the thesis is closed, is a watch call, carries a vetoed risk
    verdict, was sized at zero shares, lacks an entry or stop, or already has
    an order attached. Orders go out as brackets, so the stop is submitted with
    the entry rather than left to a later call.

    A submitted order is not a filled one. Check `get_broker_orders`.

    Args:
        thesis_id: Identifier returned by `journal_thesis`.
    """
    try:
        return execution.place_order(thesis_id)
    except (ExecutionError, JournalError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def get_broker_positions() -> dict[str, Any]:
    """Open positions at the paper broker, with unrealised P&L."""
    try:
        return execution.positions()
    except (ExecutionError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def get_broker_orders(status: str = "open", limit: int = 50) -> dict[str, Any]:
    """Orders at the paper broker, newest first.

    Args:
        status: "open", "closed" or "all".
        limit: Maximum orders to return.
    """
    try:
        return execution.orders(status=status, limit=limit)
    except (ExecutionError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def cancel_order(broker_order_id: str) -> dict[str, Any]:
    """Cancel a working order.

    Does not close a filled position — if the entry already filled, the
    position remains open and must be closed explicitly.

    Args:
        broker_order_id: Order id returned by `place_order`.
    """
    try:
        return execution.cancel_order(broker_order_id)
    except (ExecutionError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def close_broker_position(symbol: str) -> dict[str, Any]:
    """Close an open position at market.

    Closing at the broker does not close the thesis. Follow this with
    `close_thesis` and the fill price so realised R is recorded.

    Args:
        symbol: Stock symbol of the position to close.
    """
    try:
        return execution.close_position(symbol)
    except (ExecutionError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def reconcile_positions() -> dict[str, Any]:
    """Compare broker positions against open journalled theses.

    Portfolio heat is computed from the journal, so a position held without a
    thesis is exposure the risk checks cannot see. This reports both kinds of
    mismatch: untracked positions, and theses whose entry never filled.
    """
    try:
        return execution.reconcile()
    except (ExecutionError, JournalError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def score_book(
    ticker: str | None = None,
    horizon: str | None = None,
    direction: str | None = None,
    since: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Grade the closed book: expectancy, win rate, and whether conviction paid.

    Returns performance in R across every closed thesis, broken down by
    conviction, horizon, direction and outcome, plus the calibration read —
    whether stated conviction actually predicted results, and whether targets
    were set where positions were really exited.

    Every figure is computed here. Never work out expectancy, a win rate or an
    average R yourself; quote what this returns and the sample it rests on.
    Read `limitations` before drawing any conclusion — a small sample and a
    real edge produce the same numbers.

    Args:
        ticker: Optional symbol filter.
        horizon: Optional "swing", "positional" or "long_term" filter.
        direction: Optional "long", "short" or "watch" filter.
        since: Optional ISO date; only theses created on or after it.
        limit: Maximum closed theses to read.
    """
    try:
        return postmortem.score_book(
            ticker=ticker,
            horizon=horizon,
            direction=direction,
            since=since,
            limit=limit,
        )
    except (PostmortemError, JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def review_thesis(thesis_id: str) -> dict[str, Any]:
    """One call assembled for post-mortem: the plan, the result, the gap.

    Returns the thesis as it was written alongside planned R, realised R, R
    capture, holding period and how the position was exited. Falsifiers come
    back unchecked, because whether one fired is a question for the price and
    the filings rather than for the journal — go and look.

    Works on open theses too, where the useful question is whether a falsifier
    has already fired.

    Args:
        thesis_id: Identifier returned by `journal_thesis`.
    """
    try:
        return postmortem.review(thesis_id)
    except (PostmortemError, JournalError, OSError) as exc:
        return _error(exc)


@mcp.tool()
def get_institutional_holdings(institution: str, quarters: int = 2) -> dict[str, Any]:
    """A manager's 13F equity book, and what changed since the prior quarter.

    Positions are aggregated per security across sub-advisers, so a manager
    that reports the same issuer on several lines is reported once at the full
    size. Changes are computed from share counts, never values — a position
    marked down by the market was not sold.

    A 13F is long US-listed equity only, as of a quarter end, filed up to 45
    days later. Quote the period, not the present tense.

    Args:
        institution: Manager name or CIK. Names resolve through EDGAR; an
            ambiguous name returns the candidates rather than guessing.
        quarters: 13F periods to read. 2 gives the latest plus its comparison.
    """
    try:
        return smartmoney.holdings(institution, quarters=quarters)
    except (SmartMoneyError, EdgarError) as exc:
        return _error(exc)


@mcp.tool()
def get_congress_trades(
    ticker: str | None = None,
    member: str | None = None,
    year: int | None = None,
    max_reports: int = 40,
) -> dict[str, Any]:
    """Congressional stock trades disclosed under the STOCK Act.

    Amounts are the statutory bands members file, never exact figures. Report
    the range as given; there is no precise number behind it.

    Only the reports actually opened are searched, so an absent ticker means
    "not in the reports read", not "not traded". The response says how many of
    how many were read.

    Args:
        ticker: Optional symbol filter.
        member: Optional case-insensitive substring of a member's name.
        year: Disclosure year. Defaults to the current one.
        max_reports: Reports to open and parse, newest first.
    """
    try:
        return smartmoney.congress_trades(
            ticker=ticker, member=member, year=year, max_reports=max_reports
        )
    except SmartMoneyError as exc:
        return _error(exc)


@mcp.tool()
def get_congress_activity(
    year: int | None = None, max_reports: int = 40, top: int = 20
) -> dict[str, Any]:
    """Names appearing most often across recent congressional disclosures.

    Counts disclosures, not dollars or conviction: a $1,001 purchase and a
    $5,000,001 purchase count the same.

    Args:
        year: Disclosure year. Defaults to the current one.
        max_reports: Reports to open and parse, newest first.
        top: How many symbols to return.
    """
    try:
        return smartmoney.congress_activity_by_ticker(
            year=year, max_reports=max_reports, top=top
        )
    except SmartMoneyError as exc:
        return _error(exc)


@mcp.tool()
def get_price_distribution(
    ticker: str, horizon_days: int = 21, lookback_days: int = 500
) -> dict[str, Any]:
    """Where a price could be in N trading days, as a distribution.

    Drift is assumed to be zero, deliberately: a drift estimated from this much
    history carries a standard error comparable to the volatility itself. This
    forecasts the spread and refuses to forecast direction, so it can never
    support a bullish or bearish claim on its own.

    Returns Gaussian and bootstrap quantiles side by side. Where they diverge,
    the normal assumption is understating this name's tails — report the gap
    rather than averaging the two.

    Args:
        ticker: Symbol to model.
        horizon_days: Trading days ahead. 21 is roughly a month, 63 a quarter.
        lookback_days: Calendar days of history to measure volatility over.
    """
    try:
        return forecast.distribution(
            ticker, horizon_days=horizon_days, lookback_days=lookback_days
        )
    except (ForecastError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def get_path_probabilities(
    ticker: str,
    entry: float,
    stop: float,
    target: float,
    horizon_days: int = 21,
    lookback_days: int = 500,
) -> dict[str, Any]:
    """Odds of reaching the target before the stop, and the win rate required.

    Simulates the trade under zero drift and reports the probability of each
    barrier being hit first, the expected outcome in R, and the win rate the
    geometry needs to break even. A negative `edge_vs_breakeven` means the
    levels do not pay without a directional edge the model cannot see — the
    case has to come from the thesis.

    Barriers are checked at daily closes, so real stop-outs are somewhat more
    likely than reported.

    Args:
        ticker: Symbol to model.
        entry: Planned entry price.
        stop: Protective stop.
        target: Profit target.
        horizon_days: Trading days the trade is given to work.
        lookback_days: Calendar days of history to measure volatility over.
    """
    try:
        return forecast.path_probabilities(
            ticker,
            entry=entry,
            stop=stop,
            target=target,
            horizon_days=horizon_days,
            lookback_days=lookback_days,
        )
    except (ForecastError, PriceError) as exc:
        return _error(exc)


@mcp.tool()
def get_desk_health() -> dict[str, Any]:
    """The state of the desk's own record-keeping and dependencies.

    Reports the failures that are otherwise silent: theses closed without an
    exit price, open calls that have outlived their horizon, positions carrying
    no dollar risk, unparseable thesis files, and missing credentials.

    Runs offline and checks credentials for presence, not validity.
    """
    try:
        return health.desk_health()
    except (JournalError, OSError) as exc:
        return _error(exc)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
