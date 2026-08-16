"""Position sizing against the desk's limits, and the odds the levels imply.

Two questions about the same set of levels. The risk officer answers whether
the position may be taken and how large; the forecast answers whether the
geometry pays at all. They are independent, and a trade can pass one and fail
the other.
"""

from __future__ import annotations

import streamlit as st

from desk_mcp import forecast, risk
from desk_ui import charts
from desk_ui import common as ui

ui.page("Sizing & odds", "⚖️", "What the limits allow, and what the geometry implies.")
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_FAST)
def size(ticker, direction, entry, stop, equity, target, atr) -> dict:
    return risk.size_position(
        ticker=ticker,
        direction=direction,
        entry=entry,
        stop=stop,
        account_equity=equity,
        target=target,
        atr=atr,
    )


@st.cache_data(ttl=ui.TTL_FAST)
def odds(ticker, entry, stop, target, horizon) -> dict:
    return forecast.path_probabilities(
        ticker, entry=entry, stop=stop, target=target, horizon_days=horizon
    )


@st.cache_data(ttl=ui.TTL_FAST)
def policy() -> dict:
    return risk.policy()


with st.form("levels"):
    a, b, c = st.columns(3)
    ticker = a.text_input("Ticker", placeholder="NVDA").strip().upper()
    direction = b.selectbox("Direction", ["long", "short"])
    equity = c.number_input("Account equity", min_value=0.0, value=0.0, step=1000.0)

    d, e, f, g = st.columns(4)
    entry = d.number_input("Entry", min_value=0.0, value=0.0, format="%.4f")
    stop = e.number_input("Stop", min_value=0.0, value=0.0, format="%.4f")
    target = f.number_input("Target (optional)", min_value=0.0, value=0.0, format="%.4f")
    atr = g.number_input("ATR(14) (optional)", min_value=0.0, value=0.0, format="%.4f")

    horizon = st.slider("Horizon for the odds (trading days)", 5, 120, 21)
    submitted = st.form_submit_button("Size it")

if not submitted:
    st.caption(
        "Entry, stop and account equity are required. Target and ATR are "
        "optional — without them some checks cannot run, and the response says "
        "which."
    )
    limits = ui.guard(policy)
    if limits:
        st.subheader("Standing limits")
        ui.table(
            limits.get("rules", []),
            {"key": "Limit", "value": "Value", "unit": "Unit", "rationale": "Why it exists"},
        )
    st.stop()

if not ticker or entry <= 0 or stop <= 0 or equity <= 0:
    st.error(
        "Ticker, entry, stop and account equity are all required. Sizing "
        "against a placeholder account produces a share count that reads "
        "exactly like a real one."
    )
    st.stop()

# --- The verdict ------------------------------------------------------------
sizing = ui.guard(
    lambda: size(
        ticker, direction, entry, stop, equity, target or None, atr or None
    )
)

if sizing:
    verdict = sizing.get("verdict")
    banner = {
        "approved": st.success,
        "approved_with_warnings": st.warning,
        "vetoed": st.error,
    }.get(verdict, st.info)
    banner(f"**{str(verdict).replace('_', ' ').title()}**")

    # A veto is the tool's, not the reader's. Give the reason top billing.
    for veto in sizing.get("vetoes") or []:
        st.error(f"**Veto** — {veto}")
    for warning in sizing.get("warnings") or []:
        st.warning(warning)

    position = sizing.get("sizing") or {}
    levels = sizing.get("levels") or {}

    ui.metric_row(
        [
            ("Shares", position.get("shares"), None),
            ("Position value", ui.money(position.get("position_value")), None),
            ("% of equity", ui.pct(position.get("position_pct_of_equity")), None),
            ("Dollar risk", ui.money(position.get("dollar_risk")), "Loss if the stop fills"),
            ("% at risk", ui.pct(position.get("pct_equity_at_risk")), None),
        ]
    )
    ui.metric_row(
        [
            ("Risk / share", ui.money(levels.get("risk_per_share")), None),
            ("Reward / share", ui.money(levels.get("reward_per_share")), None),
            ("Reward:risk", ui.ratio(levels.get("reward_risk")), None),
            (
                "Stop in ATR",
                ui.ratio(levels.get("stop_atr_multiple")),
                "A stop inside daily noise is an exit schedule, not protection",
            ),
        ]
    )

    if position.get("capped_by"):
        st.info(
            f"Size was cut by **{position['capped_by']}**. A capped position is "
            f"a different trade from the one proposed."
        )

    st.subheader("Every check")
    ui.table(
        sizing.get("checks", []),
        {
            "rule": "Rule",
            "passed": "Passed",
            "severity": "Severity",
            "observed": "Observed",
            "threshold": "Threshold",
            "message": "What it means",
        },
    )

    portfolio = sizing.get("portfolio") or {}
    policy_limits = sizing.get("policy") or {}
    if portfolio:
        st.subheader("Portfolio effect")
        ui.metric_row(
            [
                ("Open positions", portfolio.get("open_positions"), None),
                ("Heat now", ui.pct(portfolio.get("open_heat_pct")), None),
                (
                    "Heat after this",
                    ui.pct(portfolio.get("heat_after_this_trade_pct")),
                    None,
                ),
                (
                    # Each policy entry is a Rule -- value plus the reason it
                    # exists -- not a bare number.
                    "Ceiling",
                    ui.pct((policy_limits.get("max_portfolio_heat_pct") or {}).get("value")),
                    (policy_limits.get("max_portfolio_heat_pct") or {}).get("rationale"),
                ),
            ]
        )
        st.caption(
            "Correlation is the check no formula makes: three 1% positions on "
            "the same driver are one 3% position in disguise."
        )

    ui.limitations(sizing.get("limitations"), title="Checks that did not run")

# --- The odds ---------------------------------------------------------------
if target and target > 0:
    st.subheader("What the geometry implies")

    modelled = ui.guard(lambda: odds(ticker, entry, stop, target, horizon))
    if modelled:
        bootstrap = modelled["bootstrap"]
        breakeven = modelled["breakeven"]

        odds_chart, odds_metrics = st.columns([3, 2])
        with odds_chart:
            ui.chart(
                charts.outcome_odds(
                    bootstrap["p_target_first"],
                    bootstrap["p_stop_first"],
                    bootstrap["p_neither"],
                    title=f"Where this ends up within {horizon} trading days",
                )
            )
        with odds_metrics:
            ui.metric_row([("Expected", f"{bootstrap['expected_r']}R", None)])
            ui.metric_row(
                [("Median", f"{bootstrap['median_r']}R", "The typical outcome, not the mean")]
            )

        edge = breakeven["edge_vs_breakeven"]
        needed = breakeven["win_rate_required"]
        modelled_rate = breakeven["modelled_win_rate"]

        if edge < 0:
            st.error(
                f"**Negative edge.** At {modelled['levels']['planned_r']}:1 this "
                f"needs a {needed:.1%} hit rate to break even; the model gives "
                f"{modelled_rate:.1%}. The geometry does not pay under zero "
                f"drift — the case has to come from the thesis, which this "
                f"model cannot see and will not pretend to."
            )
        else:
            st.success(
                f"**Positive edge.** Needs {needed:.1%} to break even, model "
                f"gives {modelled_rate:.1%}."
            )

        stop_read = modelled["stop_in_volatility_terms"]
        sigmas = stop_read["stop_in_daily_sigmas"]
        st.metric("Stop, in daily sigmas", ui.ratio(sigmas), help=stop_read["note"])
        if sigmas < 1.5:
            st.warning(
                "The stop sits inside routine daily noise and will be taken out "
                "by an average session, whatever the thesis says."
            )

        st.caption(
            f"Closed form with unlimited time: "
            f"{modelled['analytic_unlimited_time']['p_target_first']:.1%} — "
            f"{modelled['analytic_unlimited_time']['note']}"
        )

        ui.limitations(modelled["limitations"])
else:
    st.caption("Enter a target to model the odds.")
