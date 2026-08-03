"""Assembles price data and indicators into a single technical picture.

The chartist agent reads this. It computes nothing itself, so everything an
agent needs to judge a setup has to be here -- including the awkward parts,
like how much history was actually available and which indicators could not
be computed because of it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .indicators import core as ind
from .prices.source import BarSet, PriceError, PriceSource, default_source

DEFAULT_BENCHMARK = "SPY"


def _summarise_bars(bars: BarSet) -> dict[str, Any]:
    last = bars.bars[-1]
    volumes = bars.volumes
    avg_volume_50 = ind.sma(volumes, 50)

    return {
        "last_date": last.date,
        "last_close": last.close,
        "last_volume": last.volume,
        "avg_volume_50": round(avg_volume_50) if avg_volume_50 else None,
        "volume_vs_average": (
            round(last.volume / avg_volume_50, 2) if avg_volume_50 else None
        ),
        "bars_available": len(bars.bars),
        "first_date": bars.bars[0].date,
    }


def analyse(
    symbol: str,
    benchmark: str = DEFAULT_BENCHMARK,
    lookback_days: int = 500,
    source: PriceSource | None = None,
) -> dict[str, Any]:
    """Full technical read on a symbol.

    Anything that cannot be computed from the available history is reported as
    null with the reason recorded under `limitations`, rather than being
    approximated from a shorter window.
    """
    source = source or default_source()
    bars = source.daily_bars(
        symbol,
        start=date.today() - timedelta(days=lookback_days),
    )

    closes, highs, lows = bars.closes, bars.highs, bars.lows
    last_close = bars.bars[-1].close
    limitations: list[str] = []

    trend = ind.trend_state(closes)
    if trend.get("structure") == "insufficient history":
        limitations.append(
            f"only {len(closes)} bars available; "
            f"{', '.join(trend.get('insufficient_for', []))} could not be computed"
        )

    rsi_value = ind.rsi(closes)
    atr_value = ind.atr(highs, lows, closes)

    # Relative strength needs the benchmark aligned to the same window.
    relative: float | None = None
    try:
        benchmark_bars = source.daily_bars(
            benchmark,
            start=__import__("datetime").date.today()
            - __import__("datetime").timedelta(days=lookback_days),
        )
        relative = ind.relative_strength(closes, benchmark_bars.closes)
    except PriceError as exc:
        limitations.append(f"relative strength unavailable: {exc}")

    levels = ind.swing_levels(
        highs, lows, [b.date for b in bars.bars], current_price=last_close
    )
    support = [l for l in levels if l.acts_as == "support"]
    resistance = [l for l in levels if l.acts_as == "resistance"]

    return {
        "symbol": bars.symbol,
        "feed": bars.feed,
        "feed_note": (
            "SIP consolidated tape — volume is the full market"
            if bars.feed == "sip"
            else "IEX only — roughly 3% of consolidated volume; do not rely on "
            "volume signals"
        ),
        "retrieved_at": bars.retrieved_at,
        "price": _summarise_bars(bars),
        "trend": trend,
        "momentum": {
            "rsi_14": round(rsi_value, 2) if rsi_value is not None else None,
            "rsi_note": "above 70 conventionally overbought, below 30 oversold",
            "relative_strength_63d_vs_" + benchmark.lower(): (
                round(relative, 2) if relative is not None else None
            ),
            "drawdown_from_1y_high_pct": (
                round(ind.drawdown_from_high(closes), 2) if closes else None
            ),
        },
        "volatility": {
            "atr_14": round(atr_value, 4) if atr_value is not None else None,
            "atr_pct_of_price": (
                round(atr_value / last_close * 100, 2)
                if atr_value and last_close
                else None
            ),
            "atr_note": "stop distances are conventionally 1.5-3x ATR",
        },
        "levels": {
            # Nearest levels first -- those are the ones that matter for entry
            # and stop placement.
            "nearest_support": [
                l.to_dict() for l in sorted(support, key=lambda x: -x.price)[:3]
            ],
            "nearest_resistance": [
                l.to_dict() for l in sorted(resistance, key=lambda x: x.price)[:3]
            ],
        },
        "limitations": limitations,
    }
