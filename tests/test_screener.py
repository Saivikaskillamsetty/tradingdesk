"""Candidate ranking, tested against a fake price source.

These run offline. The ordering is the whole product here: if a laggard sorts
above a leader, every downstream agent spends its time on the wrong names and
nothing in the output looks wrong.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from desk_mcp import screener
from desk_mcp.prices.source import Bar, BarSet, PriceError
from desk_mcp.screener import ScreenerError

needs_alpaca_keys = pytest.mark.skipif(
    not (
        os.environ.get("ALPACA_API_KEY", "").strip()
        and os.environ.get("ALPACA_SECRET_KEY", "").strip()
    ),
    reason="ALPACA_API_KEY / ALPACA_SECRET_KEY not set — the live tape is unreachable",
)


def bars(closes: list[float], symbol: str = "TEST") -> BarSet:
    start = date(2025, 1, 1)
    return BarSet(
        symbol=symbol,
        timeframe="1Day",
        feed="sip",
        bars=[
            Bar(
                date=(start + timedelta(days=i)).isoformat(),
                open=c,
                high=c * 1.01,
                low=c * 0.99,
                close=c,
                volume=1_000_000,
            )
            for i, c in enumerate(closes)
        ],
        retrieved_at="2026-08-03T00:00:00Z",
    )


def ramp(start: float, end: float, count: int = 300) -> list[float]:
    step = (end - start) / (count - 1)
    return [start + step * i for i in range(count)]


class FakeSource:
    """Returns whatever closes each symbol was configured with."""

    def __init__(self, series: dict[str, list[float]], missing: set[str] | None = None):
        self._series = series
        self._missing = missing or set()

    def daily_bars(self, symbol: str, start: date, end: date | None = None) -> BarSet:
        symbol = symbol.upper()
        if symbol in self._missing:
            raise PriceError(f"{symbol}: no bars between {start} and today")
        return bars(self._series[symbol], symbol)


class TestRanking:
    def source(self, **overrides):
        series = {
            "SPY": ramp(100, 120),
            "LEADER": ramp(100, 160),
            "LAGGARD": ramp(100, 105),
        }
        series.update(overrides)
        return FakeSource(series)

    def test_orders_by_relative_strength(self):
        result = screener.rank(
            ["LAGGARD", "LEADER"], source=self.source()
        )

        assert [c["symbol"] for c in result["candidates"]] == ["LEADER", "LAGGARD"]

    def test_rank_positions_are_assigned(self):
        result = screener.rank(["LAGGARD", "LEADER"], source=self.source())

        assert [c["rank"] for c in result["candidates"]] == [1, 2]

    def test_a_stock_rising_slower_than_the_index_ranks_negative(self):
        """Up is not the same as strong."""
        result = screener.rank(["LAGGARD"], source=self.source())

        assert result["candidates"][0]["relative_strength_63d"] < 0

    def test_supporting_measures_travel_with_each_candidate(self):
        row = screener.rank(["LEADER"], source=self.source())["candidates"][0]

        for field in (
            "structure",
            "pct_from_50d",
            "rsi_14",
            "atr_pct_of_price",
            "drawdown_from_1y_high_pct",
        ):
            assert field in row

    def test_unrankable_candidates_sort_last_rather_than_vanishing(self):
        # Too little history for a 63-bar relative strength reading.
        source = self.source(SHORT=ramp(100, 110, count=20))
        result = screener.rank(["SHORT", "LEADER"], source=source)

        assert [c["symbol"] for c in result["candidates"]] == ["LEADER", "SHORT"]
        assert result["candidates"][1]["relative_strength_63d"] is None

    def test_a_failed_symbol_is_reported_not_dropped(self):
        """Absence from a shortlist must never be mistaken for rejection."""
        source = FakeSource(
            {"SPY": ramp(100, 120), "LEADER": ramp(100, 160)}, missing={"DELISTED"}
        )
        result = screener.rank(["LEADER", "DELISTED"], source=source)

        assert [c["symbol"] for c in result["candidates"]] == ["LEADER"]
        assert "DELISTED" in result["unavailable"]

    def test_benchmark_is_named_in_the_result(self):
        assert screener.rank(["LEADER"], source=self.source())["benchmark"] == "SPY"


class TestRankingRefusals:
    def test_empty_list_is_refused(self):
        with pytest.raises(ScreenerError, match="no symbols"):
            screener.rank([], source=FakeSource({}))

    def test_oversized_list_is_refused_with_the_reason(self):
        symbols = [f"S{i}" for i in range(screener.MAX_RANK_SYMBOLS + 1)]

        with pytest.raises(ScreenerError, match="exceeds"):
            screener.rank(symbols, source=FakeSource({}))

    def test_a_missing_benchmark_fails_the_whole_screen(self):
        """Without the benchmark there is no relative strength to sort on."""
        source = FakeSource({"LEADER": ramp(100, 160)}, missing={"SPY"})

        with pytest.raises(ScreenerError, match="benchmark"):
            screener.rank(["LEADER"], source=source)

    def test_every_candidate_failing_raises_rather_than_returning_empty(self):
        source = FakeSource({"SPY": ramp(100, 120)}, missing={"A", "B"})

        with pytest.raises(ScreenerError, match="no candidate"):
            screener.rank(["A", "B"], source=source)


class TestMostActive:
    def test_invalid_sort_key_is_refused(self):
        with pytest.raises(ScreenerError, match="volume"):
            screener.most_active(by="market_cap")


@pytest.mark.network
@needs_alpaca_keys
class TestAgainstLiveScreener:
    """Requires ALPACA_API_KEY and ALPACA_SECRET_KEY.

    Skipped rather than failed when the keys are absent. A suite that goes red
    for a known environmental reason teaches everyone to ignore red.
    """

    def test_movers_returns_both_sides(self):
        result = screener.movers(top=5)

        assert result["gainers"] and result["losers"]

    def test_most_active_returns_symbols(self):
        assert screener.most_active(top=5)["symbols"]
