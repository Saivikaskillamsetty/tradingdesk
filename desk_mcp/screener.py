"""Finding candidates, and cutting a list of them down to something workable.

Two different jobs live here. The first is discovery: what moved today, what
traded heavily. That comes from the venue and is nothing more than a starting
list -- a stock appearing on a movers table has no thesis attached to it, and
treating the table as a recommendation is how a desk ends up chasing.

The second is triage. A raw list of thirty symbols is not actionable, and
running the full research pass on thirty names is wasteful. `rank` scores each
symbol on the measures that survive a shortlist -- relative strength, trend
structure, distance from the highs -- so the expensive agents only see names
worth their time.

Ranking is deliberately mechanical. It orders candidates; it does not judge
them. Nothing here decides that a stock is worth owning.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from .indicators import core as ind
from .prices.source import (
    PriceError,
    PriceSource,
    alpaca_credentials,
    default_source,
)

MOVERS_URL = "https://data.alpaca.markets/v1beta1/screener/stocks/movers"
MOST_ACTIVE_URL = "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives"

MAX_RANK_SYMBOLS = 40


class ScreenerError(RuntimeError):
    """A screen could not be run."""


def _get(url: str, params: dict[str, Any]) -> dict[str, Any]:
    key, secret = alpaca_credentials()
    try:
        response = httpx.get(
            url,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params=params,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise ScreenerError(f"screener request failed: {exc}") from exc

    if response.status_code != 200:
        raise ScreenerError(
            f"screener returned HTTP {response.status_code}: {response.text[:200]}"
        )
    return response.json()


def movers(top: int = 10) -> dict[str, Any]:
    """Today's largest percentage gainers and losers."""
    payload = _get(MOVERS_URL, {"top": max(1, min(int(top), 50))})

    def rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": row["symbol"],
                "price": row.get("price"),
                "change": row.get("change"),
                "change_pct": row.get("percent_change"),
            }
            for row in items
        ]

    return {
        "as_of": payload.get("market_type", "stocks"),
        "last_updated": payload.get("last_updated"),
        "gainers": rows(payload.get("gainers") or []),
        "losers": rows(payload.get("losers") or []),
        "note": (
            "A movers table is a starting list, not a signal. Most large "
            "single-day moves are news the market has already priced, and "
            "many are in names too small or too illiquid to trade."
        ),
    }


def most_active(by: str = "volume", top: int = 10) -> dict[str, Any]:
    """The day's most heavily traded names, by share volume or trade count."""
    if by not in ("volume", "trades"):
        raise ScreenerError(f"`by` must be 'volume' or 'trades'; got {by!r}")

    payload = _get(MOST_ACTIVE_URL, {"by": by, "top": max(1, min(int(top), 50))})

    return {
        "ranked_by": by,
        "last_updated": payload.get("last_updated"),
        "symbols": [
            {
                "symbol": row["symbol"],
                "volume": row.get("volume"),
                "trade_count": row.get("trade_count"),
            }
            for row in payload.get("most_actives") or []
        ],
        "note": (
            "Heavy volume marks where attention is, not where opportunity is. "
            "The same names dominate this list most days."
        ),
    }


def _score_symbol(
    symbol: str,
    benchmark_closes: list[float],
    source: PriceSource,
    lookback_days: int,
) -> dict[str, Any]:
    """Every measure used for ranking one candidate, or the reason there is none."""
    bars = source.daily_bars(
        symbol, start=date.today() - timedelta(days=lookback_days)
    )
    closes, highs, lows = bars.closes, bars.highs, bars.lows
    last_close = closes[-1]

    trend = ind.trend_state(closes)
    atr_value = ind.atr(highs, lows, closes)
    rs = ind.relative_strength(closes, benchmark_closes)

    return {
        "symbol": bars.symbol,
        "last_close": last_close,
        "structure": trend.get("structure"),
        "pct_from_50d": (
            round(pct, 2)
            if (pct := trend.get("pct_from_fast_ma")) is not None
            else None
        ),
        "relative_strength_63d": None if rs is None else round(rs, 2),
        "rsi_14": (
            round(value, 2) if (value := ind.rsi(closes)) is not None else None
        ),
        "atr_pct_of_price": (
            round(atr_value / last_close * 100, 2)
            if atr_value and last_close
            else None
        ),
        "drawdown_from_1y_high_pct": (
            round(ind.drawdown_from_high(closes), 2) if closes else None
        ),
        "bars_available": len(bars.bars),
        "feed": bars.feed,
    }


def rank(
    symbols: list[str],
    benchmark: str = "SPY",
    lookback_days: int = 400,
    source: PriceSource | None = None,
) -> dict[str, Any]:
    """Order candidates by relative strength, with the supporting measures.

    Relative strength is the sort key because it is the one measure that
    survives a market-wide move: a stock up 4% on a day the index is up 5% is
    a laggard wearing a green candle.

    Symbols that fail are listed under `unavailable` with the reason. A
    delisted or mistyped ticker must not silently drop out of a shortlist.
    """
    if not symbols:
        raise ScreenerError("no symbols supplied")
    if len(symbols) > MAX_RANK_SYMBOLS:
        raise ScreenerError(
            f"{len(symbols)} symbols exceeds the {MAX_RANK_SYMBOLS} limit; "
            f"each one costs a separate history request"
        )

    source = source or default_source()
    start = date.today() - timedelta(days=lookback_days)

    try:
        benchmark_bars = source.daily_bars(benchmark, start=start)
    except PriceError as exc:
        raise ScreenerError(
            f"benchmark {benchmark.upper()} unavailable, so relative strength "
            f"cannot be computed for any candidate: {exc}"
        ) from exc

    scored: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}

    for symbol in symbols:
        try:
            scored.append(
                _score_symbol(
                    symbol, benchmark_bars.closes, source, lookback_days
                )
            )
        except (PriceError, ValueError, IndexError) as exc:
            unavailable[symbol.strip().upper()] = str(exc)

    if not scored:
        raise ScreenerError(
            "no candidate returned usable price history: "
            + "; ".join(f"{k}: {v}" for k, v in unavailable.items())
        )

    # Symbols with no relative strength reading sort last rather than being
    # dropped -- an unrankable candidate is still a candidate.
    ranked = sorted(
        scored,
        key=lambda row: (
            row["relative_strength_63d"] is None,
            -(row["relative_strength_63d"] or 0.0),
        ),
    )
    for position, row in enumerate(ranked, start=1):
        row["rank"] = position

    return {
        "benchmark": benchmark_bars.symbol,
        "ranked_by": "relative_strength_63d",
        "candidates": ranked,
        "unavailable": unavailable,
        "note": (
            "Ranking orders candidates; it does not judge them. A top-ranked "
            "name still needs the fundamental and technical passes before it "
            "is anything more than a symbol near the top of a list."
        ),
    }
