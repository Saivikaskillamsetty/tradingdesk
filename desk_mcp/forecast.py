"""Probabilistic forecasts, which are distributions rather than predictions.

Nothing here predicts a price. Every output is a distribution of outcomes
conditioned on how volatile the name has actually been, and the single most
important thing about it is stated up front:

**Drift is assumed to be zero.** Not because stocks do not rise, but because
drift cannot be estimated from the data available. The standard error of a
mean return estimated from a year of daily data is roughly the annual
volatility itself -- for a 30%-vol stock, a measured 12% annual drift has an
error bar of about ±30%, so the estimate is noise wearing a decimal point.
Volatility, by contrast, is estimable from the same sample to within a few
percent. So the model forecasts the spread and refuses to forecast the
direction, which is an honest description of what price history supports.

That makes this agent useless for picking a direction and genuinely useful for
the questions the desk actually needs answered: how far is this name likely to
move over the holding period, is the stop inside the noise, and does the
probability of reaching the target before the stop justify the reward:risk the
risk officer is being asked to approve.

Two models run side by side on every question:

- **Gaussian** -- log returns drawn from a normal distribution. Analytically
  clean and wrong in the tails.
- **Bootstrap** -- log returns resampled from this name's own history. Carries
  whatever skew and fat tails the stock actually exhibited.

Where the two disagree is where the normal assumption is doing damage, and the
gap is reported rather than averaged away.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import numpy as np

from .prices.source import BarSet, PriceSource, default_source

TRADING_DAYS = 252

# RiskMetrics' decay factor. Weights the last ~30 sessions most heavily, which
# is the horizon a swing trade actually lives in.
EWMA_LAMBDA = 0.94

# Enough paths that the reported probabilities are stable to well under a
# percentage point, which is finer than the input volatility is known to.
PATHS = 20_000

# Fixed so the same question returns the same answer. A probability that
# changes on refresh cannot be quoted in a thesis or checked afterwards.
SEED = 20260101

QUANTILES = (5, 16, 25, 50, 75, 84, 95)

# Volatility windows, in trading days. Short windows react to the current
# regime, long ones survive it; disagreement between them is information.
VOL_WINDOWS = (20, 60, 252)


class ForecastError(ValueError):
    """A forecast that cannot be produced as asked."""


def _log_returns(closes: list[float]) -> np.ndarray:
    prices = np.asarray(closes, dtype=float)
    if prices.size < 2:
        raise ForecastError("at least two closes are needed to measure a return")
    if np.any(prices <= 0):
        raise ForecastError("non-positive close prices cannot be logged")
    return np.diff(np.log(prices))


def _annualise(daily_sigma: float) -> float:
    return daily_sigma * math.sqrt(TRADING_DAYS)


def realised_volatility(returns: np.ndarray, window: int) -> float | None:
    """Annualised standard deviation of log returns over the last `window`.

    Returns None rather than a shorter-window figure when history is short: a
    60-day volatility computed from 30 days is not a 60-day volatility, and
    labelling it one is how a stop gets sized against the wrong number.
    """
    if returns.size < window:
        return None
    # ddof=1: this is a sample, and the population is not available.
    return round(_annualise(float(np.std(returns[-window:], ddof=1))), 4)


def ewma_volatility(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float | None:
    """Exponentially weighted annualised volatility.

    Preferred over a flat window when a regime has just changed: a fixed
    60-day window gives a shock from 59 days ago exactly the weight of
    yesterday's, so it keeps pricing a calm that has already ended.
    """
    if returns.size < 2:
        return None
    weights = lam ** np.arange(returns.size - 1, -1, -1)
    weights /= weights.sum()
    mean = float(np.sum(weights * returns))
    variance = float(np.sum(weights * (returns - mean) ** 2))
    return round(_annualise(math.sqrt(variance)), 4)


def _bars(
    symbol: str, lookback_days: int, source: PriceSource | None = None
) -> BarSet:
    source = source or default_source()
    return source.daily_bars(symbol, start=date.today() - timedelta(days=lookback_days))


def _volatility_block(returns: np.ndarray) -> dict[str, Any]:
    by_window = {
        f"realised_{w}d": realised_volatility(returns, w) for w in VOL_WINDOWS
    }
    ewma = ewma_volatility(returns)
    available = [v for v in by_window.values() if v is not None]

    return {
        **by_window,
        "ewma_94": ewma,
        "unit": "annualised standard deviation of log returns, as a decimal",
        "spread_across_windows": (
            round(max(available) - min(available), 4) if len(available) > 1 else None
        ),
        "spread_note": (
            "A wide spread means the regime changed inside the sample. The "
            "short window describes now; the long one describes what this name "
            "is normally capable of."
        ),
    }


def _sigma_for(returns: np.ndarray, block: dict[str, Any]) -> tuple[float, str]:
    """The daily volatility the forecast runs on, and where it came from."""
    ewma = block.get("ewma_94")
    if ewma:
        return ewma / math.sqrt(TRADING_DAYS), "EWMA(0.94) of daily log returns"
    fallback = float(np.std(returns, ddof=1))
    return fallback, "sample standard deviation of all available log returns"


def distribution(
    ticker: str,
    horizon_days: int = 21,
    lookback_days: int = 500,
    source: PriceSource | None = None,
) -> dict[str, Any]:
    """Where the price could be in `horizon_days`, as a distribution.

    Args:
        ticker: Symbol to model.
        horizon_days: Trading days ahead. 21 is roughly a month, 63 a quarter.
        lookback_days: Calendar days of history to measure volatility over.
        source: Price source; the default vendor is used when omitted.
    """
    if horizon_days < 1:
        raise ForecastError("horizon_days must be at least 1")

    bars = _bars(ticker, lookback_days, source)
    returns = _log_returns(bars.closes)
    spot = bars.bars[-1].close

    vol = _volatility_block(returns)
    sigma_daily, sigma_source = _sigma_for(returns, vol)
    sigma_horizon = sigma_daily * math.sqrt(horizon_days)

    rng = np.random.default_rng(SEED)

    # Gaussian: driftless geometric Brownian motion over the horizon.
    gaussian_terminal = spot * np.exp(rng.normal(0.0, sigma_horizon, PATHS))

    # Bootstrap: this name's own daily moves, resampled. Same zero-drift
    # discipline -- returns are centred so the historical drift in the sample
    # is not smuggled back in as a forecast.
    centred = returns - returns.mean()
    draws = rng.choice(centred, size=(PATHS, horizon_days), replace=True)
    bootstrap_terminal = spot * np.exp(draws.sum(axis=1))

    def quantile_block(terminal: np.ndarray) -> dict[str, Any]:
        values = np.percentile(terminal, QUANTILES)
        return {
            f"p{q}": round(float(v), 2) for q, v in zip(QUANTILES, values)
        }

    gaussian_q = quantile_block(gaussian_terminal)
    bootstrap_q = quantile_block(bootstrap_terminal)

    one_sd_pct = round(100 * (math.exp(sigma_horizon) - 1), 2)
    tail_gap = round(bootstrap_q["p5"] - gaussian_q["p5"], 2)

    return {
        "symbol": bars.symbol,
        "spot": spot,
        "as_of": bars.bars[-1].date,
        "horizon_trading_days": horizon_days,
        "volatility": vol,
        "model": {
            "sigma_daily": round(sigma_daily, 6),
            "sigma_source": sigma_source,
            "sigma_over_horizon": round(sigma_horizon, 6),
            "drift": 0.0,
            "drift_note": (
                "Deliberately zero. A drift estimated from this much history "
                "has a standard error comparable to the volatility itself, so "
                "including one would add noise and call it a forecast."
            ),
            "paths": PATHS,
            "seed": SEED,
        },
        "expected_move": {
            "one_sd_pct": one_sd_pct,
            "one_sd_range": [
                round(spot * math.exp(-sigma_horizon), 2),
                round(spot * math.exp(sigma_horizon), 2),
            ],
            "note": (
                f"About two thirds of the time the price sits inside this band "
                f"in {horizon_days} trading days, if volatility persists."
            ),
        },
        "gaussian_quantiles": gaussian_q,
        "bootstrap_quantiles": bootstrap_q,
        "tail_comparison": {
            "p5_difference": tail_gap,
            "note": (
                "Bootstrap 5th percentile minus Gaussian. Negative means this "
                "name's own history has a fatter left tail than a normal "
                "distribution allows, and the Gaussian downside is optimistic."
            ),
        },
        "limitations": [
            "This is a distribution, not a prediction. It says how far the "
            "price tends to travel, never which way.",
            "Volatility is assumed to persist over the horizon. It does not "
            "around earnings, guidance, or a Fed decision — check the calendar "
            "before quoting these bands across a known event.",
            "Estimated from daily closes only. Gaps and intraday ranges are "
            "invisible to it.",
            "The bootstrap resamples days independently, so it reproduces this "
            "name's fat tails but not volatility clustering — real drawdowns "
            "arrive in consecutive sessions more often than this model allows.",
        ],
    }


def path_probabilities(
    ticker: str,
    entry: float,
    stop: float,
    target: float,
    horizon_days: int = 21,
    lookback_days: int = 500,
    source: PriceSource | None = None,
) -> dict[str, Any]:
    """Odds of reaching the target before the stop, and what the trade is worth.

    Args:
        ticker: Symbol to model.
        entry: Planned entry price.
        stop: Protective stop.
        target: Profit target.
        horizon_days: Trading days the trade is given to work.
        lookback_days: Calendar days of history to measure volatility over.
        source: Price source; the default vendor is used when omitted.
    """
    if horizon_days < 1:
        raise ForecastError("horizon_days must be at least 1")
    for name, value in (("entry", entry), ("stop", stop), ("target", target)):
        if value is None or value <= 0:
            raise ForecastError(f"{name} must be a positive price; got {value!r}")
    if stop == entry:
        raise ForecastError("stop cannot equal entry; there would be no risk to size")

    direction = "long" if target > entry else "short"
    if direction == "long" and stop >= entry:
        raise ForecastError("a long stop must sit below the entry")
    if direction == "short" and stop <= entry:
        raise ForecastError("a short stop must sit above the entry")

    bars = _bars(ticker, lookback_days, source)
    returns = _log_returns(bars.closes)
    vol = _volatility_block(returns)
    sigma_daily, sigma_source = _sigma_for(returns, vol)

    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    planned_r = round(reward_per_share / risk_per_share, 2)

    rng = np.random.default_rng(SEED)
    centred = returns - returns.mean()

    def simulate(draws: np.ndarray) -> dict[str, Any]:
        """Walk each path day by day, stopping at whichever barrier is hit."""
        paths = entry * np.exp(np.cumsum(draws, axis=1))

        if direction == "long":
            hit_target = paths >= target
            hit_stop = paths <= stop
        else:
            hit_target = paths <= target
            hit_stop = paths >= stop

        # First index where each barrier is touched, or horizon+1 for never.
        never = draws.shape[1] + 1
        first_target = np.where(hit_target.any(axis=1), hit_target.argmax(axis=1), never)
        first_stop = np.where(hit_stop.any(axis=1), hit_stop.argmax(axis=1), never)

        target_first = first_target < first_stop
        stop_first = first_stop < first_target
        neither = ~(target_first | stop_first)

        # Unresolved paths are marked to the closing price, which is what
        # holding to the end of the horizon actually means.
        terminal = paths[:, -1]
        move = terminal - entry if direction == "long" else entry - terminal
        outcome_r = np.where(
            target_first,
            planned_r,
            np.where(stop_first, -1.0, move / risk_per_share),
        )

        return {
            "p_target_first": round(float(target_first.mean()), 4),
            "p_stop_first": round(float(stop_first.mean()), 4),
            "p_neither": round(float(neither.mean()), 4),
            "expected_r": round(float(outcome_r.mean()), 3),
            "median_r": round(float(np.median(outcome_r)), 3),
        }

    gaussian = simulate(rng.normal(0.0, sigma_daily, (PATHS, horizon_days)))
    bootstrap = simulate(rng.choice(centred, size=(PATHS, horizon_days), replace=True))

    # Gambler's ruin on a driftless walk: the probability of touching one
    # barrier before the other depends only on the log distances to each, not
    # on volatility or on how long it takes. Given unlimited time.
    log_to_target = abs(math.log(target / entry))
    log_to_stop = abs(math.log(stop / entry))
    analytic = round(log_to_stop / (log_to_target + log_to_stop), 4)

    breakeven = round(1.0 / (1.0 + planned_r), 4)
    edge = round(bootstrap["p_target_first"] - breakeven, 4)

    return {
        "symbol": bars.symbol,
        "spot": bars.bars[-1].close,
        "as_of": bars.bars[-1].date,
        "direction": direction,
        "levels": {
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_per_share": round(risk_per_share, 4),
            "reward_per_share": round(reward_per_share, 4),
            "planned_r": planned_r,
        },
        "stop_in_volatility_terms": {
            "stop_distance_pct": round(100 * risk_per_share / entry, 2),
            "daily_sigma_pct": round(100 * sigma_daily, 2),
            "stop_in_daily_sigmas": round(risk_per_share / entry / sigma_daily, 2),
            "note": (
                "How many ordinary days of movement sit between entry and stop. "
                "Below about 1.5, the stop is inside routine noise and will be "
                "taken out by the spread of an average session."
            ),
        },
        "gaussian": gaussian,
        "bootstrap": bootstrap,
        "model_disagreement": round(
            abs(bootstrap["p_target_first"] - gaussian["p_target_first"]), 4
        ),
        "analytic_unlimited_time": {
            "p_target_first": analytic,
            "note": (
                "Closed form for a driftless walk given unlimited time, which "
                "depends only on the distances to each barrier. The simulated "
                "figures are lower because the horizon runs out."
            ),
        },
        "breakeven": {
            "win_rate_required": breakeven,
            "modelled_win_rate": bootstrap["p_target_first"],
            "edge_vs_breakeven": edge,
            "note": (
                f"At {planned_r}:1 the trade needs to reach target "
                f"{breakeven:.1%} of the time to break even. A negative edge "
                f"means the geometry does not pay under a zero-drift model — "
                f"the case for the trade has to come from the thesis, not here."
            ),
        },
        "model": {
            "sigma_daily": round(sigma_daily, 6),
            "sigma_source": sigma_source,
            "drift": 0.0,
            "paths": PATHS,
            "seed": SEED,
        },
        "limitations": [
            "Zero drift. This is the probability the geometry gives with no "
            "view on direction — the thesis is what argues for direction, and "
            "this number cannot confirm it.",
            "Barriers are checked against daily closes, so an intraday spike "
            "through the stop that closes back inside is not counted. Real "
            "stop-outs are therefore more likely than `p_stop_first` implies.",
            "Volatility is held constant over the horizon. An earnings date "
            "inside the window breaks that assumption in both directions.",
            "The bootstrap resamples days independently, so it misses "
            "volatility clustering. Sequences of bad days are underweighted.",
            "A probability is not a forecast of this trade. It describes the "
            "long-run frequency across many trades with this geometry.",
        ],
    }
