"""Indicator arithmetic against hand-computed fixtures.

Offline and deterministic. An indicator that is quietly wrong produces
confident, wrong technical analysis, and nothing downstream would catch it.
"""

from __future__ import annotations

import pytest

from desk_mcp.indicators import core as ind


class TestMovingAverages:
    def test_sma_is_the_mean_of_the_window(self):
        assert ind.sma([1, 2, 3, 4, 5], 5) == 3.0

    def test_sma_uses_only_the_most_recent_window(self):
        assert ind.sma([100, 100, 1, 2, 3], 3) == 2.0

    def test_sma_returns_none_without_enough_history(self):
        """A 200-day average from 60 bars is not a 200-day average."""
        assert ind.sma([1, 2, 3], 5) is None

    def test_sma_exact_window_length(self):
        assert ind.sma([2, 4, 6], 3) == 4.0

    def test_ema_reacts_faster_than_sma_to_a_jump(self):
        """EMA weights recent bars more, so it moves first on a shock.

        A linear ramp will not show this -- EMA converges to exactly the SMA
        there -- so the fixture has to contain an actual step change.
        """
        flat_then_spike = [100.0] * 10 + [200.0]
        assert ind.ema(flat_then_spike, 5) > ind.sma(flat_then_spike, 5)

    def test_ema_equals_sma_on_a_linear_ramp(self):
        """Documents the property that makes the naive comparison misleading."""
        ramp = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert ind.ema(ramp, 5) == pytest.approx(ind.sma(ramp, 5))

    def test_ema_of_a_flat_series_is_that_value(self):
        assert ind.ema([5.0] * 20, 5) == pytest.approx(5.0)

    def test_ema_matches_hand_computation(self):
        # Seed = mean(1,2,3) = 2. k = 2/4 = 0.5.
        # bar 4: (4-2)*0.5+2 = 3.0 ; bar 5: (5-3)*0.5+3 = 4.0
        assert ind.ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)

    def test_zero_window_rejected(self):
        with pytest.raises(ValueError):
            ind.sma([1, 2, 3], 0)


class TestRSI:
    def test_uninterrupted_gains_give_100(self):
        assert ind.rsi(list(range(1, 30)), 14) == pytest.approx(100.0)

    def test_uninterrupted_losses_give_0(self):
        assert ind.rsi(list(range(30, 1, -1)), 14) == pytest.approx(0.0)

    def test_alternating_equal_moves_sit_near_50(self):
        series = [100.0]
        for i in range(40):
            series.append(series[-1] + (1 if i % 2 == 0 else -1))
        assert 40 < ind.rsi(series, 14) < 60

    def test_returns_none_without_enough_history(self):
        assert ind.rsi([1, 2, 3], 14) is None

    def test_bounded_between_0_and_100(self):
        import random

        random.seed(7)
        series = [100.0]
        for _ in range(200):
            series.append(max(1.0, series[-1] * (1 + random.uniform(-0.05, 0.05))))
        assert 0 <= ind.rsi(series, 14) <= 100


class TestTrueRangeAndATR:
    def test_true_range_uses_the_gap_when_it_is_widest(self):
        # Bar 2 gaps up: high 20, low 18, previous close 10.
        # Intraday range is 2, but |high - prev close| is 10.
        highs, lows, closes = [12, 20], [8, 18], [10, 19]
        assert ind.true_range(highs, lows, closes) == [10]

    def test_true_range_uses_intraday_range_when_no_gap(self):
        highs, lows, closes = [12, 13], [8, 9], [10, 11]
        assert ind.true_range(highs, lows, closes) == [4]

    def test_atr_of_constant_range_equals_that_range(self):
        highs = [11 + i for i in range(30)]
        lows = [9 + i for i in range(30)]
        closes = [10 + i for i in range(30)]
        # Each bar rises 1 with a 2-wide range; true range is 2 throughout.
        assert ind.atr(highs, lows, closes, 14) == pytest.approx(2.0)

    def test_atr_none_without_enough_bars(self):
        assert ind.atr([1, 2], [1, 2], [1, 2], 14) is None

    def test_mismatched_series_rejected(self):
        with pytest.raises(ValueError):
            ind.true_range([1, 2, 3], [1, 2], [1, 2, 3])


class TestRelativeStrength:
    def test_outperformance_is_positive(self):
        symbol = [100 * (1.02**i) for i in range(70)]
        benchmark = [100 * (1.01**i) for i in range(70)]
        assert ind.relative_strength(symbol, benchmark, 63) > 0

    def test_underperformance_is_negative(self):
        symbol = [100 * (1.001**i) for i in range(70)]
        benchmark = [100 * (1.01**i) for i in range(70)]
        assert ind.relative_strength(symbol, benchmark, 63) < 0

    def test_identical_series_cancel_to_zero(self):
        series = [100 * (1.01**i) for i in range(70)]
        assert ind.relative_strength(series, list(series), 63) == pytest.approx(0.0)

    def test_none_without_enough_history(self):
        assert ind.relative_strength([1, 2], [1, 2], 63) is None


class TestDrawdown:
    def test_at_the_high_is_zero(self):
        assert ind.drawdown_from_high([1, 2, 3, 4, 5]) == pytest.approx(0.0)

    def test_below_the_high_is_negative(self):
        assert ind.drawdown_from_high([100, 200, 150]) == pytest.approx(-25.0)


class TestSwingLevels:
    def test_repeated_pivots_cluster_into_one_level(self):
        # Three peaks at ~110 and troughs at ~90.
        highs, lows, dates = [], [], []
        for i in range(30):
            peak = i % 10 == 5
            trough = i % 10 == 0
            highs.append(110 if peak else 100)
            lows.append(90 if trough else 100)
            dates.append(f"2026-01-{i + 1:02d}")

        levels = ind.swing_levels(highs, lows, dates)
        resistance = [l for l in levels if l.kind == "resistance"]

        assert resistance
        assert resistance[0].price == pytest.approx(110, abs=1)
        assert resistance[0].touches >= 2

    def test_single_touch_survives_only_while_recent(self):
        """Within a short history every pivot is recent, so it counts.

        Supersedes an earlier rule that dropped all single-touch pivots
        outright. That rule erased every level on a trending stock; recency
        is what distinguishes a usable untested level from a stale one, and
        `test_stale_single_touch_pivots_are_still_dropped` covers the far side.
        """
        highs = [100.0] * 20
        highs[10] = 150.0
        lows = [100.0] * 20
        dates = [f"2026-01-{i + 1:02d}" for i in range(20)]

        levels = ind.swing_levels(highs, lows, dates)
        spike = [l for l in levels if l.price > 140]

        assert spike, "a pivot inside a 20-bar history is recent by definition"
        assert spike[0].touches == 1, "and must be flagged as untested"

    def test_flat_series_produces_no_levels(self):
        """A plateau is not a pivot.

        Testing `>=` alone marks every bar of a flat stretch as an extreme,
        which fabricates levels on consolidating or illiquid names.
        """
        flat = [100.0] * 40
        dates = [f"2026-01-{i + 1:02d}" for i in range(40)]

        assert ind.swing_levels(flat, flat, dates) == []

    def test_equal_highs_separated_by_lower_bars_still_cluster(self):
        """A genuine double top must survive the plateau fix."""
        highs, lows, dates = [], [], []
        for i in range(40):
            top = i in (10, 25)
            highs.append(120.0 if top else 100.0)
            lows.append(100.0)
            dates.append(f"2026-02-{i + 1:02d}")

        resistance = [
            l for l in ind.swing_levels(highs, lows, dates) if l.kind == "resistance"
        ]

        assert len(resistance) == 1
        assert resistance[0].price == pytest.approx(120.0)
        assert resistance[0].touches == 2

    def test_broken_resistance_is_reported_as_support(self):
        """Once price trades above an old ceiling, that level is a floor.

        Reporting it as resistance below the current price invites the
        opposite trade.
        """
        highs, lows, dates = [], [], []
        for i in range(40):
            top = i in (10, 25)
            highs.append(120.0 if top else 100.0)
            lows.append(100.0)
            dates.append(f"2026-02-{i + 1:02d}")

        levels = ind.swing_levels(highs, lows, dates, current_price=150.0)
        level = next(l for l in levels if l.price == pytest.approx(120.0))

        assert level.kind == "resistance", "how it formed is preserved"
        assert level.acts_as == "support", "what it does now"
        assert level.distance_pct == pytest.approx(-20.0)

    def test_level_above_price_still_acts_as_resistance(self):
        highs, lows, dates = [], [], []
        for i in range(40):
            top = i in (10, 25)
            highs.append(120.0 if top else 100.0)
            lows.append(100.0)
            dates.append(f"2026-02-{i + 1:02d}")

        levels = ind.swing_levels(highs, lows, dates, current_price=110.0)
        level = next(l for l in levels if l.price == pytest.approx(120.0))

        assert level.acts_as == "resistance"
        assert level.distance_pct == pytest.approx(9.09, abs=0.1)

    def test_annotation_omitted_when_no_price_given(self):
        highs = [100.0] * 40
        highs[10] = highs[25] = 120.0
        dates = [f"2026-02-{i + 1:02d}" for i in range(40)]

        levels = ind.swing_levels(highs, [100.0] * 40, dates)
        assert all(l.acts_as is None for l in levels)

    def test_trending_stock_still_yields_recent_levels(self):
        """A two-touch rule everywhere erases all structure in a trend.

        Each swing low in an uptrend prints at a new price, so nothing
        clusters and every recent level is filtered out -- leaving only the
        congestion zone the stock left long ago. On a real name that ran
        108 -> 488 this put "nearest support" 45% below spot.
        """
        highs, lows, dates = [], [], []
        for i in range(200):
            base = 100.0 + i * 2  # persistent uptrend, no revisited prices
            swing_low = i % 20 == 10
            swing_high = i % 20 == 0
            highs.append(base + (8 if swing_high else 2))
            lows.append(base - (8 if swing_low else 2))
            dates.append(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}")

        price = lows[-1] + 4
        levels = ind.swing_levels(highs, lows, dates, current_price=price)
        support = [l for l in levels if l.acts_as == "support"]

        assert support, "a trending stock must still produce support levels"

        nearest = max(support, key=lambda l: l.price)
        assert nearest.distance_pct > -25, (
            f"nearest support {nearest.distance_pct:.1f}% away is unusable "
            f"for stop placement"
        )

    def test_untested_recent_pivot_is_marked_single_touch(self):
        """touches=1 is the caller's signal that a level is untested."""
        highs, lows, dates = [], [], []
        for i in range(120):
            base = 100.0 + i
            highs.append(base + (6 if i % 20 == 0 else 1))
            lows.append(base - (6 if i % 20 == 10 else 1))
            dates.append(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}")

        levels = ind.swing_levels(highs, lows, dates)
        assert any(l.touches == 1 for l in levels)

    def test_stale_single_touch_pivots_are_still_dropped(self):
        """Recency is what earns an untested pivot its place, not novelty."""
        highs, lows, dates = [], [], []
        for i in range(200):
            # One isolated spike very early, then a long flat stretch.
            spike = i == 5
            highs.append(300.0 if spike else 100.0)
            lows.append(50.0 if spike else 100.0)
            dates.append(f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}")

        levels = ind.swing_levels(highs, lows, dates, recent_bars=60)

        assert not [l for l in levels if l.price > 250], (
            "a single touch from 200 bars ago should not survive"
        )

    def test_short_series_yields_nothing(self):
        assert ind.swing_levels([1, 2], [1, 2], ["a", "b"]) == []


class TestTrendState:
    def test_rising_series_is_an_uptrend(self):
        closes = [100 + i for i in range(250)]
        state = ind.trend_state(closes)

        assert state["structure"] == "uptrend"
        assert state["above_fast_ma"] and state["above_slow_ma"]
        assert state["fast_above_slow"]

    def test_falling_series_is_a_downtrend(self):
        closes = [500 - i for i in range(250)]
        assert ind.trend_state(closes)["structure"] == "downtrend"

    def test_short_history_reports_insufficient_not_a_guess(self):
        state = ind.trend_state([100 + i for i in range(60)])

        assert state["structure"] == "insufficient history"
        assert "sma_200" in state["insufficient_for"]
        assert state["sma_50"] is not None

    def test_percent_distance_from_moving_averages(self):
        closes = [100.0] * 199 + [110.0]
        state = ind.trend_state(closes)

        # SMA200 is very close to 100, so last close sits ~10% above it.
        assert state["pct_from_slow_ma"] == pytest.approx(9.5, abs=0.6)
