"""The environment, and what is moving in it."""

from __future__ import annotations

import streamlit as st

from desk_mcp import forecast, macro, screener
from desk_ui import common as ui

ui.page("Market", "🌐", "Macro conditions, movers, and relative strength.")
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_FAST)
def snapshot() -> dict:
    return macro.snapshot()


@st.cache_data(ttl=ui.TTL_FAST)
def movers(top: int) -> dict:
    return screener.movers(top=top)


@st.cache_data(ttl=ui.TTL_FAST)
def active(by: str, top: int) -> dict:
    return screener.most_active(by=by, top=top)


@st.cache_data(ttl=ui.TTL_FAST)
def ranked(symbols: tuple[str, ...], benchmark: str) -> dict:
    return screener.rank(list(symbols), benchmark=benchmark)


@st.cache_data(ttl=ui.TTL_FAST)
def spread(ticker: str, horizon: int) -> dict:
    return forecast.distribution(ticker, horizon_days=horizon)


macro_tab, screen_tab, odds_tab = st.tabs(["Macro", "Screener", "Expected move"])

# --- Macro ------------------------------------------------------------------
with macro_tab:
    reading = ui.guard(snapshot)
    if reading:
        readings = reading.get("readings") or {}

        def change(item: dict, window: str) -> str:
            moved = (item.get("changes") or {}).get(window) or {}
            value = moved.get("change")
            if value is None:
                return "—"
            return f"{value:+.2f}"

        rows = [
            {
                "Series": item.get("label", key),
                "Value": f"{item.get('value')} {item.get('unit', '')}".strip(),
                "As of": item.get("as_of"),
                "1m": change(item, "1m"),
                "3m": change(item, "3m"),
                "12m": change(item, "12m"),
                "Reads as": item.get("reads_as", ""),
                "FRED id": item.get("series_id"),
            }
            for key, item in readings.items()
            if isinstance(item, dict)
        ]
        ui.table(rows, empty="No macro readings returned.")

        stale = [
            item.get("label", key)
            for key, item in readings.items()
            if isinstance(item, dict) and (item.get("days_since_observation") or 0) > 45
        ]
        if stale:
            st.warning(
                "Published with a lag, so these describe a month that has "
                f"already ended: {', '.join(stale)}."
            )

        ui.provenance(
            f"Source: {reading.get('source')}",
            f"Retrieved {reading.get('retrieved_at', '')[:19]}",
        )

        notes = [reading["note"]] if reading.get("note") else []
        unavailable = reading.get("unavailable") or {}
        notes += [f"**{k}** — {v}" for k, v in unavailable.items()]
        ui.limitations(notes)

# --- Screener ---------------------------------------------------------------
with screen_tab:
    top = st.slider("How many", 5, 50, 10)

    gainers_losers, most_active = st.columns(2)

    with gainers_losers:
        st.markdown("**Movers**")
        if st.button("Load movers"):
            found = ui.guard(lambda: movers(top))
            if found:
                st.markdown("Gainers")
                ui.table(found.get("gainers", []))
                st.markdown("Losers")
                ui.table(found.get("losers", []))

    with most_active:
        st.markdown("**Most active**")
        by = st.selectbox("By", ["volume", "trades"])
        if st.button("Load most active"):
            found = ui.guard(lambda: active(by, top))
            if found:
                ui.table(found.get("symbols", []))

    st.divider()
    st.markdown("**Rank a list by relative strength**")
    raw = st.text_input("Symbols, comma separated", placeholder="NVDA, AMD, AVGO, MU")
    benchmark = st.text_input("Benchmark", value="SPY")

    if raw and st.button("Rank"):
        symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
        result = ui.guard(lambda: ranked(symbols, benchmark.strip().upper() or "SPY"))
        if result:
            ui.table(result.get("candidates") or [], empty="Nothing could be ranked.")
            ui.provenance(
                f"Ranked by {result.get('ranked_by')}",
                f"against {result.get('benchmark')}",
            )
            notes = [result["note"]] if result.get("note") else []
            notes += [
                f"**{k}** — {v}" for k, v in (result.get("unavailable") or {}).items()
            ]
            notes.append(
                "This ranks a list you supplied. It is not a screen — there is "
                "no fundamental universe behind it, and a themed list assembled "
                "from memory is a recollection, not a screen."
            )
            ui.limitations(notes)

# --- Expected move ----------------------------------------------------------
with odds_tab:
    a, b = st.columns([2, 1])
    ticker = a.text_input("Ticker", key="dist_ticker").strip().upper()
    horizon = b.slider("Trading days", 5, 120, 21)

    if ticker:
        result = ui.guard(lambda: spread(ticker, horizon))
        if result:
            move = result["expected_move"]
            volatility = result["volatility"]

            ui.metric_row(
                [
                    ("Spot", ui.money(result["spot"]), result["as_of"]),
                    ("1σ move", ui.pct(move["one_sd_pct"]), move["note"]),
                    ("Low", ui.money(move["one_sd_range"][0]), None),
                    ("High", ui.money(move["one_sd_range"][1]), None),
                ]
            )

            st.markdown("**Realised volatility, annualised**")
            ui.metric_row(
                [
                    ("20d", ui.ratio(volatility["realised_20d"]), None),
                    ("60d", ui.ratio(volatility["realised_60d"]), None),
                    ("252d", ui.ratio(volatility["realised_252d"]), None),
                    ("EWMA", ui.ratio(volatility["ewma_94"]), "Weights recent sessions most"),
                ]
            )
            if volatility.get("spread_across_windows"):
                st.caption(volatility["spread_note"])

            st.markdown("**Where the price could be**")
            quantiles = [
                {
                    "Percentile": q,
                    "Gaussian": result["gaussian_quantiles"][f"p{q}"],
                    "Bootstrap": result["bootstrap_quantiles"][f"p{q}"],
                }
                for q in forecast.QUANTILES
            ]
            ui.table(quantiles)

            gap = result["tail_comparison"]["p5_difference"]
            if gap < 0:
                st.warning(
                    f"Bootstrap 5th percentile is {abs(gap):.2f} **below** the "
                    f"Gaussian one: this name's own history has a fatter left "
                    f"tail than a normal distribution allows, so the Gaussian "
                    f"downside is optimistic."
                )
            else:
                st.caption(result["tail_comparison"]["note"])

            st.error(
                "**This says how far, never which way.** Drift is assumed to be "
                "zero, because a drift estimated from this much history carries "
                "a standard error comparable to the volatility itself."
            )
            ui.limitations(result["limitations"])
