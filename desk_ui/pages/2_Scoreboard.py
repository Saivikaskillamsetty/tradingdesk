"""How the desk has actually done, and whether its confidence was justified."""

from __future__ import annotations

import streamlit as st

from desk_mcp import journal, postmortem
from desk_ui import common as ui

ui.page(
    "Scoreboard",
    "📊",
    "Expectancy in R, and whether conviction predicted anything.",
)
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_FAST)
def score(ticker: str | None, horizon: str | None, direction: str | None) -> dict:
    return postmortem.score_book(
        ticker=ticker or None, horizon=horizon or None, direction=direction or None
    )


@st.cache_data(ttl=ui.TTL_FAST)
def review(thesis_id: str) -> dict:
    return postmortem.review(thesis_id)


@st.cache_data(ttl=ui.TTL_FAST)
def closed() -> list[dict]:
    return journal.search(status="closed", limit=500)


a, b, c = st.columns(3)
ticker = a.text_input("Ticker filter", "").strip().upper()
horizon = b.selectbox("Horizon", ["", *journal.HORIZONS])
direction = c.selectbox("Direction", ["", *journal.DIRECTIONS])

book = ui.guard(lambda: score(ticker, horizon, direction))
if book is None:
    st.stop()

overall = book["overall"]

if overall["count"] == 0:
    st.info(
        "No closed call carries a realised R yet, so there is nothing to score. "
        "This page becomes useful once a few resolve with an exit price."
    )
    if book["unscored"]:
        st.markdown("**Closed but unscoreable**")
        ui.table(book["unscored"])
    ui.limitations(book["limitations"])
    st.stop()

# --- Headline ---------------------------------------------------------------
ui.metric_row(
    [
        ("Scored", overall["count"], "Closed calls with a realised R"),
        ("Expectancy", f"{overall['expectancy_r']}R", "Mean result per call — the number that matters"),
        ("Win rate", ui.pct((overall["win_rate"] or 0) * 100), "Meaningless without expectancy beside it"),
        ("Payoff", ui.ratio(overall["payoff_ratio"]), "Average win over average loss"),
        ("Total", f"{overall['total_r']}R", None),
    ]
)

if overall["count"] < postmortem.MIN_SAMPLE:
    st.warning(
        f"**{overall['count']} scored calls is below the "
        f"{postmortem.MIN_SAMPLE} needed for any of this to mean anything.** "
        f"Read the figures as descriptive, not predictive. One lucky trade can "
        f"still flip the sign of expectancy at this sample."
    )

# --- Calibration ------------------------------------------------------------
st.subheader("Calibration")

calibration = book["calibration"]
ordering = calibration["conviction_ordering_holds"]

if ordering is True:
    st.success(
        "**Conviction predicted outcome.** Expectancy rose with stated "
        "conviction, so sizing up on it was paying for a signal that is "
        "actually there."
    )
elif ordering is False:
    st.error(
        "**Conviction did not predict outcome.** High-conviction calls did not "
        "out-earn low-conviction ones, which means sizing up on conviction was "
        "paying for a signal that is not there. This is the finding the desk "
        "least wants and most needs."
    )
else:
    st.caption("Only one conviction bucket carries data — no ordering to test yet.")

ui.metric_row(
    [
        (
            "Avg R capture",
            ui.ratio(calibration["avg_r_capture"]),
            "Realised R over planned R. Below 1: targets sit beyond where "
            "positions actually get exited.",
        ),
        ("Avg days held", ui.ratio(calibration["avg_days_held"]), None),
        ("Sample", calibration["scored_sample"], None),
    ]
)

capture = calibration["avg_r_capture"]
if capture is not None and capture < 0.6:
    st.warning(
        f"R capture of {capture} means positions are being exited well short "
        f"of their targets — every reward:risk the risk officer approved was "
        f"optimistic by roughly that factor."
    )


def bucket_rows(block: dict) -> list[dict]:
    return [
        {
            "Bucket": name,
            "Calls": stats["count"],
            "Expectancy": f"{stats['expectancy_r']}R" if stats["expectancy_r"] is not None else "—",
            "Win rate": ui.pct((stats["win_rate"] or 0) * 100) if stats["win_rate"] is not None else "—",
            "Total": f"{stats['total_r']}R" if stats["total_r"] is not None else "—",
            "Best": stats["best_r"],
            "Worst": stats["worst_r"],
        }
        for name, stats in block.items()
    ]


for label, key in (
    ("By conviction", "by_conviction"),
    ("By horizon", "by_horizon"),
    ("By direction", "by_direction"),
    ("By outcome", "by_outcome"),
):
    st.markdown(f"**{label}**")
    ui.table(bucket_rows(book[key]))

# --- What is not counted ----------------------------------------------------
st.subheader("Not scored")

if book["unscored"]:
    st.caption(
        "Excluded from every figure above. The ones missing an exit price are "
        "a record-keeping failure, not a data limitation."
    )
    ui.table(book["unscored"])
else:
    st.caption("Every closed call produced a realised R.")

watch = book["watch_calls"]
if watch["closed"]:
    st.caption(
        f"{watch['closed']} closed watch calls carry no R by design: "
        f"{watch['by_outcome']}. Whether staying out was right is not in the "
        f"journal — it has to be checked against what the name did."
    )

ui.limitations(book["limitations"])

# --- One call ---------------------------------------------------------------
st.subheader("Review one call")

resolved = ui.guard(closed) or []
if resolved:
    chosen = st.selectbox(
        "Closed thesis",
        [t["id"] for t in resolved],
        format_func=lambda i: next(
            (f"{t['ticker']} — {t['id']}" for t in resolved if t["id"] == i), i
        ),
    )
    if chosen:
        detail = ui.guard(lambda: review(chosen))
        if detail:
            thesis = detail["thesis"]
            derived = detail["derived"]

            st.markdown(f"**{thesis['ticker']}** — {thesis['text']}")
            ui.metric_row(
                [
                    ("Planned", f"{derived['planned_r']}R" if derived["planned_r"] else "—", None),
                    ("Realised", f"{derived['realised_r']}R" if derived["realised_r"] is not None else "—", None),
                    ("Capture", ui.ratio(derived["r_capture"]), "Realised over planned"),
                    ("Days held", derived["days_held"], None),
                    ("Exit", derived["exit_discipline"] or "—", "plan_ran, discretionary, or thesis_lapsed"),
                ]
            )

            st.markdown("**Falsifiers, as written — go and check whether each fired**")
            for item in detail["falsifiers_to_check"]:
                st.markdown(f"- {item}")

            if detail["evidence_as_cited"]:
                st.markdown("**Evidence, as cited at the time**")
                ui.table(detail["evidence_as_cited"])

            if detail["gaps_at_the_time"]:
                st.markdown("**Known gaps when the call was made**")
                for item in detail["gaps_at_the_time"]:
                    st.markdown(f"- {item}")

            if detail["close_note"]:
                st.info(f"**Close note** — {detail['close_note']}")

            ui.limitations(detail["limitations"])
