"""Probability models, tested against series whose true parameters are known.

These run offline against a synthetic price source. A forecasting module is
unusually easy to get subtly wrong -- an annualisation factor dropped, a
barrier compared the wrong way round -- and every such error produces output
that looks entirely reasonable. So the tests check recovery of parameters that
were put in deliberately, and check the simulation against a closed form that
can be worked out by hand.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from desk_mcp import forecast
from desk_mcp.forecast import ForecastError
from desk_mcp.prices.source import Bar, BarSet


def series(closes: list[float], symbol: str = "TEST") -> BarSet:
    start = date(2024, 1, 1)
    return BarSet(
        symbol=symbol,
        timeframe="1Day",
        feed="sip",
        retrieved_at="2026-01-01T00:00:00+00:00",
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
    )


def gbm(sigma_annual: float = 0.40, n: int = 520, spot: float = 100.0, seed: int = 7):
    """A driftless walk with a known annualised volatility."""
    rng = np.random.default_rng(seed)
    daily = sigma_annual / math.sqrt(forecast.TRADING_DAYS)
    closes = spot * np.exp(np.cumsum(rng.normal(0.0, daily, n)))
    return series([float(c) for c in closes])


class Source:
    """A price source that returns one prepared BarSet."""

    def __init__(self, bars: BarSet) -> None:
        self.bars = bars

    def daily_bars(self, symbol, start, end=None) -> BarSet:
        return self.bars


class TestVolatilityMeasurement:
    def test_recovers_the_volatility_it_was_given(self):
        bars = gbm(sigma_annual=0.40)
        returns = forecast._log_returns(bars.closes)

        assert forecast.realised_volatility(returns, 252) == pytest.approx(0.40, abs=0.05)

    def test_a_calm_series_measures_calm(self):
        bars = gbm(sigma_annual=0.10)
        returns = forecast._log_returns(bars.closes)

        assert forecast.realised_volatility(returns, 252) == pytest.approx(0.10, abs=0.02)

    def test_a_window_longer_than_the_history_returns_nothing(self):
        """A 252-day vol from 30 days is not a 252-day vol."""
        returns = forecast._log_returns(gbm(n=30).closes)

        assert forecast.realised_volatility(returns, 252) is None

    def test_ewma_tracks_a_regime_change_faster_than_a_flat_window(self):
        calm = gbm(sigma_annual=0.10, n=400, seed=1).closes
        shock = gbm(sigma_annual=0.80, n=40, spot=calm[-1], seed=2).closes
        returns = forecast._log_returns(calm + shock)

        ewma = forecast.ewma_volatility(returns)
        annual = forecast.realised_volatility(returns, 252)

        assert ewma > annual

    def test_non_positive_prices_are_refused(self):
        with pytest.raises(ForecastError, match="non-positive"):
            forecast._log_returns([10.0, 0.0, 5.0])

    def test_a_single_close_cannot_produce_a_return(self):
        with pytest.raises(ForecastError, match="at least two"):
            forecast._log_returns([10.0])


class TestDistribution:
    def test_the_median_sits_at_spot_because_drift_is_zero(self):
        bars = gbm()
        result = forecast.distribution("TEST", horizon_days=21, source=Source(bars))

        assert result["model"]["drift"] == 0.0
        assert result["gaussian_quantiles"]["p50"] == pytest.approx(
            result["spot"], rel=0.03
        )

    def test_the_band_widens_with_the_horizon(self):
        source = Source(gbm())
        month = forecast.distribution("TEST", horizon_days=21, source=source)
        quarter = forecast.distribution("TEST", horizon_days=63, source=source)

        assert quarter["expected_move"]["one_sd_pct"] > month["expected_move"]["one_sd_pct"]

    def test_the_band_scales_with_the_root_of_time(self):
        source = Source(gbm())
        one = forecast.distribution("TEST", horizon_days=10, source=source)
        four = forecast.distribution("TEST", horizon_days=40, source=source)

        # Loose enough to absorb the 6-decimal rounding on each figure, tight
        # enough that anything but root-of-time scaling fails.
        assert four["model"]["sigma_over_horizon"] == pytest.approx(
            2 * one["model"]["sigma_over_horizon"], rel=1e-4
        )

    def test_quantiles_are_ordered(self):
        result = forecast.distribution("TEST", horizon_days=21, source=Source(gbm()))
        values = [result["bootstrap_quantiles"][f"p{q}"] for q in forecast.QUANTILES]

        assert values == sorted(values)

    def test_both_models_are_reported(self):
        """Averaging them would hide exactly the disagreement worth seeing."""
        result = forecast.distribution("TEST", horizon_days=21, source=Source(gbm()))

        assert result["gaussian_quantiles"] and result["bootstrap_quantiles"]
        assert "p5_difference" in result["tail_comparison"]

    def test_the_same_question_returns_the_same_answer(self):
        """A probability that moves on refresh cannot be quoted in a thesis."""
        source = Source(gbm())
        first = forecast.distribution("TEST", horizon_days=21, source=source)
        second = forecast.distribution("TEST", horizon_days=21, source=source)

        assert first["gaussian_quantiles"] == second["gaussian_quantiles"]

    def test_a_zero_horizon_is_refused(self):
        with pytest.raises(ForecastError, match="at least 1"):
            forecast.distribution("TEST", horizon_days=0, source=Source(gbm()))


class TestPathProbabilities:
    def levels(self, bars, **overrides):
        spot = bars.bars[-1].close
        payload = {
            "entry": spot,
            "stop": spot * 0.95,
            "target": spot * 1.10,
            "horizon_days": 21,
        }
        payload.update(overrides)
        return payload

    def test_matches_the_closed_form_for_unlimited_time(self):
        """Gambler's ruin: the odds depend only on the log distances."""
        bars = gbm()
        result = forecast.path_probabilities(
            "TEST", source=Source(bars), **self.levels(bars)
        )

        expected = abs(math.log(0.95)) / (abs(math.log(1.10)) + abs(math.log(0.95)))

        assert result["analytic_unlimited_time"]["p_target_first"] == pytest.approx(
            expected, abs=0.001
        )

    def test_a_finite_horizon_reaches_the_target_less_often(self):
        bars = gbm()
        result = forecast.path_probabilities(
            "TEST", source=Source(bars), **self.levels(bars)
        )

        assert (
            result["bootstrap"]["p_target_first"]
            < result["analytic_unlimited_time"]["p_target_first"]
        )

    def test_probabilities_and_the_unresolved_share_account_for_everything(self):
        bars = gbm()
        result = forecast.path_probabilities(
            "TEST", source=Source(bars), **self.levels(bars)
        )
        block = result["gaussian"]

        assert (
            block["p_target_first"] + block["p_stop_first"] + block["p_neither"]
        ) == pytest.approx(1.0, abs=0.001)

    def test_a_longer_horizon_leaves_fewer_trades_unresolved(self):
        bars = gbm()
        short = forecast.path_probabilities(
            "TEST", source=Source(bars), **self.levels(bars, horizon_days=5)
        )
        long = forecast.path_probabilities(
            "TEST", source=Source(bars), **self.levels(bars, horizon_days=60)
        )

        assert long["bootstrap"]["p_neither"] < short["bootstrap"]["p_neither"]

    def test_breakeven_win_rate_follows_the_reward_to_risk(self):
        bars = gbm()
        spot = bars.bars[-1].close
        result = forecast.path_probabilities(
            "TEST",
            source=Source(bars),
            entry=spot,
            stop=spot * 0.95,
            target=spot * 1.10,
            horizon_days=21,
        )

        # 2:1 needs a third of trades to reach target simply to break even.
        assert result["levels"]["planned_r"] == pytest.approx(2.0, abs=0.05)
        assert result["breakeven"]["win_rate_required"] == pytest.approx(
            1 / 3, abs=0.01
        )

    def test_a_tight_stop_is_measured_in_daily_sigmas(self):
        bars = gbm(sigma_annual=0.40)
        spot = bars.bars[-1].close
        result = forecast.path_probabilities(
            "TEST",
            source=Source(bars),
            entry=spot,
            stop=spot * 0.995,
            target=spot * 1.10,
            horizon_days=21,
        )

        assert result["stop_in_volatility_terms"]["stop_in_daily_sigmas"] < 1.5

    def test_a_tighter_stop_is_hit_more_often(self):
        bars = gbm()
        spot = bars.bars[-1].close
        common = {"entry": spot, "target": spot * 1.10, "horizon_days": 21}

        tight = forecast.path_probabilities(
            "TEST", source=Source(bars), stop=spot * 0.98, **common
        )
        wide = forecast.path_probabilities(
            "TEST", source=Source(bars), stop=spot * 0.90, **common
        )

        assert tight["bootstrap"]["p_stop_first"] > wide["bootstrap"]["p_stop_first"]

    def test_a_short_is_modelled_in_its_own_direction(self):
        bars = gbm()
        spot = bars.bars[-1].close
        result = forecast.path_probabilities(
            "TEST",
            source=Source(bars),
            entry=spot,
            stop=spot * 1.05,
            target=spot * 0.90,
            horizon_days=21,
        )

        assert result["direction"] == "short"
        assert result["bootstrap"]["p_target_first"] > 0


class TestPathRefusals:
    def test_a_long_stop_above_the_entry_is_refused(self):
        bars = gbm()
        with pytest.raises(ForecastError, match="long stop must sit below"):
            forecast.path_probabilities(
                "TEST", entry=100.0, stop=105.0, target=120.0, source=Source(bars)
            )

    def test_a_short_stop_below_the_entry_is_refused(self):
        bars = gbm()
        with pytest.raises(ForecastError, match="short stop must sit above"):
            forecast.path_probabilities(
                "TEST", entry=100.0, stop=95.0, target=80.0, source=Source(bars)
            )

    def test_a_stop_at_the_entry_is_refused(self):
        bars = gbm()
        with pytest.raises(ForecastError, match="no risk to size"):
            forecast.path_probabilities(
                "TEST", entry=100.0, stop=100.0, target=120.0, source=Source(bars)
            )

    def test_a_negative_price_is_refused(self):
        bars = gbm()
        with pytest.raises(ForecastError, match="positive price"):
            forecast.path_probabilities(
                "TEST", entry=100.0, stop=-5.0, target=120.0, source=Source(bars)
            )
