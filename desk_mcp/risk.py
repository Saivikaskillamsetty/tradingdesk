"""Position sizing and the desk's risk limits, computed here so agents never do.

Sizing is where a good thesis loses money. The arithmetic is trivial and
therefore tempting to do in a prompt, but a share count that is wrong by a
factor of ten looks exactly like one that is right, and the error only
surfaces after the position exists.

Every limit is a named rule with a stated rationale, so a veto can be
explained by quoting the rule rather than by an agent's judgement. Rules
are deliberately expressed as policy rather than advice: the risk officer's
answer to "but the setup looks great" is that the limit does not care.

Portfolio heat is read from the journal, so exposure the desk never recorded
is exposure this module cannot see -- that gap is reported, not glossed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from desk_mcp import journal


class RiskError(ValueError):
    """A sizing request that is malformed, rather than merely unwise."""


@dataclass(frozen=True)
class Rule:
    """A limit, and why it exists."""

    key: str
    value: float
    unit: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


POLICY: dict[str, Rule] = {
    "max_risk_pct_per_trade": Rule(
        key="max_risk_pct_per_trade",
        value=1.0,
        unit="% of account equity",
        rationale=(
            "Caps the damage from any single wrong call. At 1% a run of ten "
            "consecutive losses costs roughly a tenth of the account, which "
            "is survivable; at 5% it is not."
        ),
    ),
    "max_position_pct": Rule(
        key="max_position_pct",
        value=20.0,
        unit="% of account equity",
        rationale=(
            "A tight stop makes a very large position look cheap in risk "
            "terms, but gaps do not respect stops. This caps exposure to a "
            "single overnight event."
        ),
    ),
    "max_portfolio_heat_pct": Rule(
        key="max_portfolio_heat_pct",
        value=6.0,
        unit="% of account equity at risk across open positions",
        rationale=(
            "Correlated positions tend to stop out together. Six percent is "
            "the total loss the desk accepts if every open thesis fails at "
            "once."
        ),
    ),
    "min_reward_risk": Rule(
        key="min_reward_risk",
        value=2.0,
        unit="ratio",
        rationale=(
            "Below 2:1 the strategy needs a win rate above 50% just to break "
            "even after costs, and no one on this desk has demonstrated one."
        ),
    ),
    "min_stop_atr_multiple": Rule(
        key="min_stop_atr_multiple",
        value=1.5,
        unit="x ATR(14)",
        rationale=(
            "A stop inside ordinary daily noise is taken out by the noise. "
            "This is the most common way a correct thesis still loses money."
        ),
    ),
}


@dataclass(frozen=True)
class Check:
    """One rule, evaluated against this specific trade."""

    rule: str
    passed: bool
    severity: str  # "veto" or "warn"
    observed: float | None
    threshold: float | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def _validate(
    direction: str,
    entry: float,
    stop: float,
    account_equity: float,
) -> None:
    """Reject requests that are incoherent, as opposed to merely risky.

    These raise rather than veto: a stop on the wrong side of the entry is not
    a trade the desk declines, it is a request that does not describe a trade.
    """
    if direction not in ("long", "short"):
        raise RiskError(f"direction must be 'long' or 'short'; got {direction!r}")
    if account_equity <= 0:
        raise RiskError("account equity must be positive")
    if entry <= 0 or stop <= 0:
        raise RiskError("entry and stop must be positive prices")
    if entry == stop:
        raise RiskError("entry and stop are the same price; risk per share is zero")
    if direction == "long" and stop > entry:
        raise RiskError(
            f"a long stop must sit below the entry; got stop {stop} above entry {entry}"
        )
    if direction == "short" and stop < entry:
        raise RiskError(
            f"a short stop must sit above the entry; got stop {stop} below entry {entry}"
        )


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
    """Size a position against the desk's limits and rule on whether it passes.

    Returns the share count, the capital genuinely at risk, every rule checked
    with its observed value, and a verdict. Anything that could not be checked
    is listed under `limitations` rather than assumed to have passed.
    """
    ticker = ticker.strip().upper()
    direction = direction.strip().lower()
    _validate(direction, entry, stop, account_equity)

    checks: list[Check] = []
    limitations: list[str] = []

    max_risk = POLICY["max_risk_pct_per_trade"]
    requested_risk_pct = max_risk.value if risk_pct is None else float(risk_pct)
    applied_risk_pct = min(requested_risk_pct, max_risk.value)
    if requested_risk_pct > max_risk.value:
        checks.append(
            Check(
                rule="max_risk_pct_per_trade",
                passed=False,
                severity="warn",
                observed=requested_risk_pct,
                threshold=max_risk.value,
                message=(
                    f"requested {requested_risk_pct}% of equity at risk, above the "
                    f"{max_risk.value}% limit; sized at {max_risk.value}% instead"
                ),
            )
        )

    # --- sizing -----------------------------------------------------------
    risk_per_share = abs(entry - stop)
    risk_budget = account_equity * applied_risk_pct / 100.0
    shares = math.floor(risk_budget / risk_per_share)
    capped_by: str | None = None

    max_position = POLICY["max_position_pct"]
    max_position_value = account_equity * max_position.value / 100.0
    if shares * entry > max_position_value:
        shares = math.floor(max_position_value / entry)
        capped_by = "max_position_pct"
        checks.append(
            Check(
                rule="max_position_pct",
                passed=False,
                severity="warn",
                observed=_round(shares * entry / account_equity * 100.0),
                threshold=max_position.value,
                message=(
                    "the risk budget allowed a larger position than the "
                    f"concentration limit permits; size cut to {shares} shares "
                    f"({max_position.value}% of equity)"
                ),
            )
        )

    position_value = shares * entry
    dollar_risk = shares * risk_per_share
    pct_equity_at_risk = dollar_risk / account_equity * 100.0

    if shares < 1:
        checks.append(
            Check(
                rule="max_risk_pct_per_trade",
                passed=False,
                severity="veto",
                observed=0,
                threshold=1,
                message=(
                    f"a {applied_risk_pct}% risk budget of "
                    f"${risk_budget:,.2f} does not cover one share at "
                    f"${risk_per_share:,.2f} of risk; the account is too small "
                    f"for this stop distance"
                ),
            )
        )

    # --- reward to risk ---------------------------------------------------
    min_rr = POLICY["min_reward_risk"]
    reward_per_share: float | None = None
    reward_risk: float | None = None

    if target is None:
        limitations.append(
            "no target supplied, so reward:risk was not evaluated — ask the "
            "chartist for a target before treating this as approved"
        )
        checks.append(
            Check(
                rule="min_reward_risk",
                passed=False,
                severity="warn",
                observed=None,
                threshold=min_rr.value,
                message="no target supplied; reward:risk could not be checked",
            )
        )
    else:
        wrong_side = (direction == "long" and target <= entry) or (
            direction == "short" and target >= entry
        )
        if wrong_side:
            checks.append(
                Check(
                    rule="min_reward_risk",
                    passed=False,
                    severity="veto",
                    observed=None,
                    threshold=min_rr.value,
                    message=(
                        f"target {target} is on the losing side of entry {entry} "
                        f"for a {direction}; this is not a trade"
                    ),
                )
            )
        else:
            reward_per_share = abs(target - entry)
            reward_risk = reward_per_share / risk_per_share
            passed = reward_risk >= min_rr.value
            checks.append(
                Check(
                    rule="min_reward_risk",
                    passed=passed,
                    severity="veto",
                    observed=_round(reward_risk),
                    threshold=min_rr.value,
                    message=(
                        f"reward:risk {reward_risk:.2f}:1"
                        + (
                            " meets the minimum"
                            if passed
                            else f" is below the {min_rr.value}:1 minimum; the "
                            "setup does not justify the risk however good the "
                            "chart looks"
                        )
                    ),
                )
            )

    # --- stop versus noise -------------------------------------------------
    min_atr = POLICY["min_stop_atr_multiple"]
    atr_multiple: float | None = None
    if atr is None or atr <= 0:
        limitations.append(
            "no ATR supplied, so the stop was not checked against ordinary "
            "daily noise"
        )
        checks.append(
            Check(
                rule="min_stop_atr_multiple",
                passed=False,
                severity="warn",
                observed=None,
                threshold=min_atr.value,
                message="no ATR supplied; stop distance could not be checked",
            )
        )
    else:
        atr_multiple = risk_per_share / atr
        passed = atr_multiple >= min_atr.value
        checks.append(
            Check(
                rule="min_stop_atr_multiple",
                passed=passed,
                severity="veto",
                observed=_round(atr_multiple),
                threshold=min_atr.value,
                message=(
                    f"stop sits {atr_multiple:.2f}x ATR from entry"
                    + (
                        ""
                        if passed
                        else f", inside the {min_atr.value}x minimum; ordinary "
                        "noise will take it out"
                    )
                ),
            )
        )

    # --- portfolio heat ----------------------------------------------------
    heat_rule = POLICY["max_portfolio_heat_pct"]
    exposure = journal.open_risk()
    open_dollar_risk = float(exposure["total_dollar_risk"])
    open_heat_pct = open_dollar_risk / account_equity * 100.0
    heat_after_pct = (open_dollar_risk + dollar_risk) / account_equity * 100.0

    heat_ok = heat_after_pct <= heat_rule.value
    checks.append(
        Check(
            rule="max_portfolio_heat_pct",
            passed=heat_ok,
            severity="veto",
            observed=_round(heat_after_pct),
            threshold=heat_rule.value,
            message=(
                f"{exposure['open_positions']} open position(s) already risk "
                f"${open_dollar_risk:,.2f} ({open_heat_pct:.2f}% of equity); "
                f"this trade takes total heat to {heat_after_pct:.2f}%"
                + ("" if heat_ok else f", above the {heat_rule.value}% ceiling")
            ),
        )
    )
    limitations.append(
        "portfolio heat counts only journalled open theses; any position "
        "taken without recording it is invisible to this check"
    )

    # --- verdict -----------------------------------------------------------
    vetoes = [c for c in checks if not c.passed and c.severity == "veto"]
    warnings = [c for c in checks if not c.passed and c.severity == "warn"]

    if vetoes:
        verdict = "vetoed"
    elif warnings:
        verdict = "approved_with_warnings"
    else:
        verdict = "approved"

    return {
        "ticker": ticker,
        "direction": direction,
        "account_equity": account_equity,
        "levels": {
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk_per_share": _round(risk_per_share, 4),
            "reward_per_share": _round(reward_per_share, 4),
            "reward_risk": _round(reward_risk),
            "stop_atr_multiple": _round(atr_multiple),
        },
        "sizing": {
            "shares": shares,
            "position_value": _round(position_value),
            "position_pct_of_equity": _round(
                position_value / account_equity * 100.0
            ),
            "dollar_risk": _round(dollar_risk),
            "pct_equity_at_risk": _round(pct_equity_at_risk),
            "risk_pct_applied": applied_risk_pct,
            "capped_by": capped_by,
        },
        "portfolio": {
            "open_theses": exposure["open_theses"],
            "open_positions": exposure["open_positions"],
            "open_dollar_risk": open_dollar_risk,
            "open_heat_pct": _round(open_heat_pct),
            "heat_after_this_trade_pct": _round(heat_after_pct),
            "positions": exposure["positions"],
        },
        "checks": [c.to_dict() for c in checks],
        "verdict": verdict,
        "vetoes": [c.message for c in vetoes],
        "warnings": [c.message for c in warnings],
        "policy": {k: r.to_dict() for k, r in POLICY.items()},
        "limitations": limitations,
    }


def policy() -> dict[str, Any]:
    """The desk's standing risk limits, with the reasoning behind each."""
    return {"rules": [r.to_dict() for r in POLICY.values()]}
