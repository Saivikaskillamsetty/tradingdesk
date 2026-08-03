"""MCP server backing the trading desk's research agents.

Every tool here returns values with provenance attached. Agents are instructed
never to state a figure they did not receive from one of these calls, and to
cite the period it covers -- see .claude/agents/*.md.
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from desk_mcp import metrics, technicals
from desk_mcp.edgar import facts, filings
from desk_mcp.edgar.client import EdgarError
from desk_mcp.edgar.concepts import ALL_KEYS
from desk_mcp.prices.source import PriceError

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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
