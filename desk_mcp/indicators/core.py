"""Technical indicators as pure functions over price series.

Agents read these; they never compute them. A moving average worked out in a
model's head is unverifiable and wrong often enough to matter.

Every function returns `None` rather than a partial figure when there is not
enough history -- a 200-day average computed from 60 bars is not a 200-day
average, and silently returning one produces confident nonsense.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Sequence


@dataclass(frozen=True)
class Level:
    """A support or resistance level and the evidence for it.

    `kind` records how the level formed. `acts_as` records what it does now,
    which is the useful field: once price breaks above an old resistance, that
    level becomes support, and reporting it as resistance below the current
    price invites exactly the wrong read.
    """

    price: float
    kind: str
    touches: int
    last_touch: str
    acts_as: str | None = None
    distance_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sma(values: Sequence[float], window: int) -> float | None:
    """Simple moving average of the most recent `window` values."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values: Sequence[float], window: int) -> float | None:
    """Exponential moving average, seeded with an SMA of the first window."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return None

    multiplier = 2 / (window + 1)
    result = sum(values[:window]) / window
    for value in values[window:]:
        result = (value - result) * multiplier + result
    return result


def rsi(values: Sequence[float], window: int = 14) -> float | None:
    """Wilder's Relative Strength Index.

    Uses Wilder's smoothing rather than a simple average of gains and losses,
    which is what charting packages plot -- a simple average gives visibly
    different numbers and would not match what the user sees.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window + 1:
        return None

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window

    for i in range(window, len(deltas)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window

    if avg_loss == 0:
        # No downside over the window: RSI is defined as 100.
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    """True range per bar, accounting for gaps between sessions."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows and closes must be the same length")
    if len(highs) < 2:
        return []

    ranges = []
    for i in range(1, len(highs)):
        previous_close = closes[i - 1]
        ranges.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - previous_close),
                abs(lows[i] - previous_close),
            )
        )
    return ranges


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> float | None:
    """Average True Range, Wilder-smoothed.

    The desk's stop distances are expressed in ATR multiples, so this feeds
    position sizing rather than being decorative.
    """
    ranges = true_range(highs, lows, closes)
    if len(ranges) < window:
        return None

    result = sum(ranges[:window]) / window
    for value in ranges[window:]:
        result = (result * (window - 1) + value) / window
    return result


def relative_strength(
    symbol_closes: Sequence[float],
    benchmark_closes: Sequence[float],
    window: int = 63,
) -> float | None:
    """Performance versus a benchmark over `window` bars, in percentage points.

    Positive means the symbol outpaced the benchmark. 63 bars is roughly one
    quarter. This is the single most useful swing filter: buying strength
    relative to the index beats buying absolute strength.
    """
    if len(symbol_closes) < window + 1 or len(benchmark_closes) < window + 1:
        return None

    def change(series: Sequence[float]) -> float | None:
        start = series[-(window + 1)]
        if start == 0:
            return None
        return (series[-1] - start) / start * 100.0

    symbol_change = change(symbol_closes)
    benchmark_change = change(benchmark_closes)
    if symbol_change is None or benchmark_change is None:
        return None
    return symbol_change - benchmark_change


def drawdown_from_high(closes: Sequence[float], window: int = 252) -> float | None:
    """How far below its running high the last close sits, in percent."""
    if not closes:
        return None
    recent = closes[-window:]
    peak = max(recent)
    if peak == 0:
        return None
    return (recent[-1] - peak) / peak * 100.0


def swing_levels(
    highs: Sequence[float],
    lows: Sequence[float],
    dates: Sequence[str],
    lookback: int = 3,
    tolerance_pct: float = 1.5,
    current_price: float | None = None,
    recent_bars: int = 60,
) -> list[Level]:
    """Support and resistance from clustered swing pivots.

    A pivot is a bar whose high exceeds (or low undercuts) the `lookback` bars
    either side. Pivots within `tolerance_pct` of each other form one level,
    and repeated touches are what make a level worth respecting.

    Requiring two touches everywhere sounds right and is badly wrong for
    trending stocks. In a strong trend each successive swing low prints at a
    different price, so nothing clusters and every recent level is discarded --
    leaving only the stale congestion zone the stock left months ago. On a name
    that ran from 108 to 488, that put the "nearest support" 45% below spot,
    which is useless for placing a stop.

    So a pivot inside the last `recent_bars` survives on a single touch. It is
    reported with `touches=1`, which is the caller's signal that the level is
    untested -- a recent swing low is the standard stop reference whether or
    not price has returned to it. Older pivots still need corroboration.
    """
    if not (len(highs) == len(lows) == len(dates)):
        raise ValueError("highs, lows and dates must be the same length")
    if len(highs) < lookback * 2 + 1:
        return []

    pivots: list[tuple[float, str, str]] = []
    for i in range(lookback, len(highs) - lookback):
        window = range(i - lookback, i + lookback + 1)

        # A bar must be the extreme of its window *and* stand out from it.
        # Testing only `>=` marks every bar of a flat stretch as a pivot,
        # which manufactures dozens of phantom levels on a consolidating or
        # illiquid name. Requiring it to exceed the window minimum keeps
        # genuine double tops (equal highs with lower bars between them)
        # while discarding plateaus.
        high_window = [highs[j] for j in window]
        if highs[i] >= max(high_window) and highs[i] > min(high_window):
            pivots.append((highs[i], "resistance", dates[i]))

        low_window = [lows[j] for j in window]
        if lows[i] <= min(low_window) and lows[i] < max(low_window):
            pivots.append((lows[i], "support", dates[i]))

    # Pivots at or after this date count even when untested.
    recent_cutoff = dates[-recent_bars] if len(dates) > recent_bars else dates[0]

    levels: list[Level] = []
    for kind in ("support", "resistance"):
        candidates = sorted(
            (p for p in pivots if p[1] == kind), key=lambda p: p[0]
        )
        cluster: list[tuple[float, str, str]] = []

        def flush(group: list[tuple[float, str, str]]) -> None:
            if not group:
                return
            # An untested pivot only counts if it is recent enough to still
            # describe where price is trading.
            if len(group) < 2 and max(g[2] for g in group) < recent_cutoff:
                return
            prices = [g[0] for g in group]
            levels.append(
                Level(
                    price=round(sum(prices) / len(prices), 4),
                    kind=kind,
                    touches=len(group),
                    last_touch=max(g[2] for g in group),
                )
            )

        for pivot in candidates:
            if cluster and abs(pivot[0] - cluster[0][0]) / cluster[0][0] * 100 > tolerance_pct:
                flush(cluster)
                cluster = []
            cluster.append(pivot)
        flush(cluster)

    if current_price is not None and current_price > 0:
        levels = [
            Level(
                price=level.price,
                kind=level.kind,
                touches=level.touches,
                last_touch=level.last_touch,
                # What the level does now, regardless of how it formed.
                acts_as="support" if level.price < current_price else "resistance",
                distance_pct=round(
                    (level.price - current_price) / current_price * 100, 2
                ),
            )
            for level in levels
        ]

    return sorted(levels, key=lambda level: level.price)


def trend_state(
    closes: Sequence[float],
    fast: int = 50,
    slow: int = 200,
) -> dict[str, Any]:
    """Where price sits relative to its moving-average structure.

    Reports the arrangement rather than a buy/sell call -- interpretation is
    the chartist agent's job, and encoding it here would hide the reasoning.
    """
    last = closes[-1] if closes else None
    fast_ma = sma(closes, fast)
    slow_ma = sma(closes, slow)

    state: dict[str, Any] = {
        "last_close": last,
        f"sma_{fast}": fast_ma,
        f"sma_{slow}": slow_ma,
        "bars_available": len(closes),
    }

    if last is None or fast_ma is None or slow_ma is None:
        state["structure"] = "insufficient history"
        state["insufficient_for"] = [
            name
            for name, value, need in (
                (f"sma_{fast}", fast_ma, fast),
                (f"sma_{slow}", slow_ma, slow),
            )
            if value is None
        ]
        return state

    above_fast = last > fast_ma
    above_slow = last > slow_ma
    stacked_up = fast_ma > slow_ma

    if above_fast and above_slow and stacked_up:
        structure = "uptrend"
    elif not above_fast and not above_slow and not stacked_up:
        structure = "downtrend"
    else:
        structure = "mixed"

    state.update(
        {
            "structure": structure,
            "above_fast_ma": above_fast,
            "above_slow_ma": above_slow,
            "fast_above_slow": stacked_up,
            "pct_from_fast_ma": (last - fast_ma) / fast_ma * 100,
            "pct_from_slow_ma": (last - slow_ma) / slow_ma * 100,
        }
    )
    return state
