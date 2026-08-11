"""One name: what it earns, what it is worth watching for, and what it filed."""

from __future__ import annotations

import streamlit as st

from desk_mcp import metrics, technicals
from desk_mcp.edgar import facts, filings
from desk_ui import common as ui

ui.page("Research", "🔎", "Financials from SEC filings, technicals from the tape.")
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_SLOW)
def statement(ticker: str, period: str) -> dict:
    return facts.statement(ticker, period=period)


@st.cache_data(ttl=ui.TTL_SLOW)
def derived(ticker: str) -> dict:
    return metrics.analyse(ticker)


@st.cache_data(ttl=ui.TTL_FAST)
def technical(ticker: str) -> dict:
    return technicals.analyse(ticker)


@st.cache_data(ttl=ui.TTL_SLOW)
def recent(ticker: str, forms: tuple[str, ...] | None) -> list[dict]:
    return filings.recent_filings(ticker, forms=list(forms) if forms else None, limit=25)


left, right = st.columns([3, 1])
ticker = left.text_input("Ticker", value="", placeholder="NVDA").strip().upper()
period = right.selectbox("Period", ["annual", "quarterly"], index=0)

if not ticker:
    st.caption("Enter a ticker. Financials need no API key; technicals need Alpaca.")
    st.stop()

financials, derived_metrics, technical_read, filing_rows = st.tabs(
    ["Financials", "Metrics", "Technicals", "Filings"]
)

# --- Financials -------------------------------------------------------------
with financials:
    data = ui.guard(lambda: statement(ticker, period))
    if data:
        st.subheader(data.get("company_name") or ticker)
        items = data.get("facts") or {}

        rows = [
            {
                "Line item": item["label"],
                "Value": ui.money(item["value"]),
                "Period end": item["period_end"],
                "FY": f"{item['fiscal_year']} {item['fiscal_period']}",
                "Form": item["form"],
                "XBRL concept": item["concept"],
            }
            for item in items.values()
        ]
        ui.table(rows)

        ui.provenance(
            f"CIK {data.get('cik')}",
            f"{len(items)} line items",
            "Every value carries the concept it was resolved from — US-GAAP "
            "concepts drift per company and over time, which is why the "
            "concept column is shown rather than assumed.",
        )

        unavailable = data.get("unavailable") or {}
        if unavailable:
            ui.limitations(
                [f"**{k}** — {v}" for k, v in unavailable.items()],
                title="Line items not available for this company",
            )

# --- Metrics ----------------------------------------------------------------
with derived_metrics:
    profile = ui.guard(lambda: derived(ticker))
    if profile:
        st.subheader(f"{profile.get('company_name') or ticker} — FY{profile.get('fiscal_year')}")

        rows = [
            {
                "Metric": m["label"],
                # Always a string. A column mixing formatted text with raw
                # floats cannot be serialised for display at all.
                "Value": (
                    ui.pct(m["value"]) if m["unit"] == "percent"
                    else ui.ratio(m["value"]) if m["unit"] in ("ratio", "x")
                    else ui.money(m["value"]) if m["unit"] == "USD"
                    else "—" if m["value"] is None
                    else str(m["value"])
                ),
                "Period": m.get("period_end"),
                "Note": m.get("note") or "",
            }
            for m in profile.get("metrics", [])
        ]
        ui.table(rows)
        ui.provenance(
            f"Period end {profile.get('period_end')}",
            "Computed in Python from resolved XBRL facts — nothing estimated.",
        )

        unavailable = profile.get("unavailable") or {}
        if unavailable:
            ui.limitations(
                [f"**{k}** — {v}" for k, v in unavailable.items()],
                title="Metrics that could not be computed",
            )

# --- Technicals -------------------------------------------------------------
with technical_read:
    read = ui.guard(lambda: technical(ticker))
    if read:
        price = read["price"]
        volatility = read["volatility"]
        momentum = read["momentum"]

        ui.metric_row(
            [
                ("Last", ui.money(price["last_close"]), price["last_date"]),
                ("ATR(14)", ui.ratio(volatility["atr_14"]), volatility["atr_note"]),
                ("ATR % of price", ui.pct(volatility["atr_pct_of_price"]), None),
                ("RSI(14)", ui.ratio(momentum["rsi_14"]), momentum["rsi_note"]),
            ]
        )

        trend = read["trend"]
        st.markdown(f"**Trend** — {trend.get('structure', 'unknown')}")
        st.json(trend, expanded=False)

        levels = read["levels"]
        support, resistance = st.columns(2)
        with support:
            st.markdown("**Nearest support**")
            ui.table(levels["nearest_support"], empty="None identified below price.")
        with resistance:
            st.markdown("**Nearest resistance**")
            ui.table(levels["nearest_resistance"], empty="None identified above price.")

        ui.provenance(read["feed_note"], f"Retrieved {read['retrieved_at'][:19]}")
        ui.limitations(read.get("limitations"))

        st.page_link("pages/3_Sizing.py", label="Size a position on these levels →")

# --- Filings ----------------------------------------------------------------
with filing_rows:
    choices = st.multiselect(
        "Form types", ["10-K", "10-Q", "8-K", "4", "SC 13D", "SC 13G", "DEF 14A"]
    )
    found = ui.guard(lambda: recent(ticker, tuple(choices) if choices else None))
    if found is not None:
        ui.table(
            found,
            {
                "form": "Form",
                "meaning": "Means",
                "filed": "Filed",
                "period": "Period",
                "url": "Document",
            },
            empty="No filings matched.",
        )
        ui.limitations(
            [
                "Filing text is text. Nothing here is parsed into figures — a "
                "number read out of filing prose has no XBRL concept behind it, "
                "so prefer the Financials tab wherever the figure exists there."
            ]
        )
