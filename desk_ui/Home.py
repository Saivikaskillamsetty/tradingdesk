"""Desk home: what is open, what it is carrying, and whether the book is intact.

Run with:

    uv run --extra ui streamlit run desk_ui/Home.py
"""

from __future__ import annotations

import streamlit as st

from desk_mcp import health, journal, postmortem
from desk_ui import charts
from desk_ui import common as ui

ui.page(
    "The desk",
    "📓",
    "Open calls, portfolio heat, and the state of the desk's own record-keeping.",
)
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_FAST)
def open_book() -> dict:
    return journal.open_risk()


@st.cache_data(ttl=ui.TTL_FAST)
def theses(status: str | None) -> list[dict]:
    return journal.search(status=status, limit=500)


@st.cache_data(ttl=ui.TTL_FAST)
def desk_health() -> dict:
    return health.desk_health()


@st.cache_data(ttl=ui.TTL_FAST)
def scoreboard() -> dict:
    return postmortem.score_book()


exposure = ui.guard(open_book)
report = ui.guard(desk_health)

if exposure is None or report is None:
    st.stop()

# --- Health first -----------------------------------------------------------
# The book's numbers are only worth reading if the book is intact, so anything
# corrupting them is stated before the numbers themselves.
status = report["status"]
errors = [f for f in report["findings"] if f["severity"] == "error"]
warnings = [f for f in report["findings"] if f["severity"] == "warning"]

state, banner = st.columns([1, 4])
with state:
    ui.chip(status if status != "ok" else "good", {
        "ok": "Book intact",
        "warning": "Worth fixing",
        "error": "Book compromised",
    }[status])

with banner:
    if status == "error":
        st.markdown(
            f"**{len(errors)} problem{'s' if len(errors) != 1 else ''} corrupting "
            f"the figures below.**"
        )
    elif status == "warning":
        st.caption(f"{len(warnings)} thing{'s' if len(warnings) != 1 else ''} worth fixing.")

if status == "error":
    for finding in errors:
        st.markdown(f"- {finding['finding']}")

# --- Exposure ---------------------------------------------------------------
st.subheader("Exposure as recorded")

# The hero figure: the one number this view leads with. Capital at risk is it
# — everything else on the page qualifies it.
lead, rest = st.columns([1, 3])
with lead:
    ui.hero(
        "Capital at risk",
        ui.money(exposure["total_dollar_risk"]),
        "if every open position stops out at once",
    )
with rest:
    ui.metric_row(
        [
            ("Open calls", exposure["open_theses"], "Positions and watch calls together"),
            ("Positions", exposure["open_positions"], "Calls carrying dollar risk"),
            ("Watch only", exposure["watch_only"], "No position taken"),
        ]
    )

ui.limitations(
    [
        "This is exposure as *recorded*. A position taken without journalling "
        "it is invisible here, so portfolio heat is understated by whatever "
        "was never written down.",
    ]
)

# --- The open book ----------------------------------------------------------
st.subheader("Open")

live = ui.guard(lambda: theses("open")) or []

if not live:
    st.caption("Nothing open. An empty book is a valid state, not a missing one.")
else:
    rows = [
        {
            "id": t["id"],
            "ticker": t["ticker"],
            "direction": t.get("direction"),
            "horizon": t.get("horizon"),
            "conviction": t.get("conviction"),
            "opened": (t.get("created_at") or "")[:10],
            "entry": (t.get("levels") or {}).get("entry"),
            "stop": (t.get("levels") or {}).get("stop"),
            "target": (t.get("levels") or {}).get("target"),
            "dollar_risk": (t.get("sizing") or {}).get("dollar_risk"),
        }
        for t in live
    ]
    ui.table(rows)

    stale = report["journal"]["stale_open_theses"]
    if stale:
        st.warning(
            f"{len(stale)} of these have outlived their horizon — the oldest "
            f"({stale[0]['ticker']}) by "
            f"{stale[0]['age_days'] - stale[0]['stale_after_days']} days. Each "
            f"still counts against heat."
        )

# --- Record ------------------------------------------------------------------
st.subheader("Record")

book = ui.guard(scoreboard)
if book:
    overall = book["overall"]
    if overall["count"] == 0:
        st.caption(
            "No closed calls carry a realised R yet, so there is nothing to "
            "score. The scoreboard becomes meaningful after a few resolve."
        )
    else:
        resolved = ui.guard(lambda: theses("closed")) or []
        ui.chart(charts.cumulative_r(resolved, title="Realised R, cumulative"))

        ui.metric_row(
            [
                ("Scored calls", overall["count"], None),
                ("Expectancy", f"{overall['expectancy_r']}R", "Mean result per call"),
                ("Win rate", ui.pct((overall["win_rate"] or 0) * 100), None),
                ("Total", f"{overall['total_r']}R", None),
            ]
        )
        if overall["count"] < postmortem.MIN_SAMPLE:
            st.info(
                f"{overall['count']} scored calls is below the "
                f"{postmortem.MIN_SAMPLE} needed before these figures mean "
                f"anything. Descriptive, not predictive."
            )

st.page_link("pages/2_Scoreboard.py", label="Full scoreboard and calibration →")

# --- Instruments -------------------------------------------------------------
st.subheader("Instruments")

credentials = report["credentials"]
missing = credentials["missing"]

if missing:
    st.caption("Missing credentials, and what each blocks:")
    for item in missing:
        st.markdown(f"- `{item['variable']}` — {item['blocks']}")
else:
    st.caption("All credentials present.")

cache_state = report["cache"]
st.caption(
    f"Cache: {cache_state['entries']} entries, "
    f"{cache_state.get('size_mb', 0)} MB · Book: "
    f"{report['journal']['total_theses']} theses at "
    f"`{report['journal']['directory']}`"
)

ui.limitations(report["limitations"])

if st.button("Refresh"):
    st.cache_data.clear()
    st.rerun()
