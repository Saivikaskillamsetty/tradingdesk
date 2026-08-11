"""Recording and closing calls.

The only page here that writes. Two rules from the journal carry into the
form design rather than being left to discipline: a thesis will not record
without a falsifier, and a position will not close honestly without an exit
price. The form enforces the first and argues loudly for the second.
"""

from __future__ import annotations

import streamlit as st

from desk_mcp import journal
from desk_ui import common as ui

ui.page("Journal", "✍️", "Write a call down, or resolve one.")
ui.sidebar_note()

blocked = ui.read_only_notice()


def refresh() -> None:
    st.cache_data.clear()


record_tab, close_tab, browse_tab = st.tabs(["Record", "Close", "Browse"])

# --- Record -----------------------------------------------------------------
with record_tab:
    st.caption(
        "Usually `/analyze` does this. Record here when you want a view on the "
        "book directly."
    )

    with st.form("record"):
        a, b, c, d = st.columns(4)
        ticker = a.text_input("Ticker").strip().upper()
        direction = b.selectbox("Direction", journal.DIRECTIONS, index=2)
        horizon = c.selectbox("Horizon", journal.HORIZONS)
        conviction = d.selectbox("Conviction", ["low", "medium", "high"], index=1)

        thesis = st.text_area(
            "The call, in plain language",
            placeholder="What this is and what to do about it.",
        )

        e, f, g = st.columns(3)
        entry = e.number_input("Entry", min_value=0.0, value=0.0, format="%.4f")
        stop = f.number_input("Stop", min_value=0.0, value=0.0, format="%.4f")
        target = g.number_input("Target", min_value=0.0, value=0.0, format="%.4f")

        h, i = st.columns(2)
        shares = h.number_input("Shares", min_value=0, value=0, step=1)
        dollar_risk = i.number_input("Dollar risk", min_value=0.0, value=0.0)

        falsifiers_raw = st.text_area(
            "Falsifiers — one per line. Required.",
            placeholder=(
                "daily close below $228.68\n"
                "gross margin below 44% for a second consecutive quarter"
            ),
            help=(
                "A call nothing could disprove cannot be scored, only "
                "rationalised. 'If the story changes' is not a falsifier."
            ),
        )
        gaps_raw = st.text_area("Known gaps — one per line", placeholder="")

        submitted = st.form_submit_button("Record", disabled=blocked)

    if submitted:
        falsifiers = [line.strip() for line in falsifiers_raw.splitlines() if line.strip()]
        if not falsifiers:
            st.error(
                "At least one falsifier is required. If you cannot name what "
                "would make you abandon this, the thesis is not yet scoreable."
            )
        else:
            result = ui.guard(
                lambda: journal.record(
                    ticker=ticker,
                    thesis=thesis,
                    direction=direction,
                    horizon=horizon,
                    conviction=conviction,
                    entry=entry or None,
                    stop=stop or None,
                    target=target or None,
                    shares=int(shares) or None,
                    dollar_risk=dollar_risk or None,
                    falsifiers=falsifiers,
                    gaps=[g.strip() for g in gaps_raw.splitlines() if g.strip()],
                )
            )
            if result:
                refresh()
                st.success(f"Recorded **{result['id']}**")
                st.caption("Keep this id — it is what closes the call later.")

# --- Close ------------------------------------------------------------------
with close_tab:
    open_calls = ui.guard(lambda: journal.search(status="open", limit=500)) or []

    if not open_calls:
        st.caption("Nothing open to close.")
    else:
        chosen = st.selectbox(
            "Open thesis",
            [t["id"] for t in open_calls],
            format_func=lambda i: next(
                (
                    f"{t['ticker']} · {t.get('direction')} · {t['id']}"
                    for t in open_calls
                    if t["id"] == i
                ),
                i,
            ),
        )

        entry = next((t for t in open_calls if t["id"] == chosen), None)
        if entry:
            st.markdown(f"**{entry['ticker']}** — {entry['thesis']}")
            levels = entry.get("levels") or {}
            st.caption(
                f"Entry {levels.get('entry')} · Stop {levels.get('stop')} · "
                f"Target {levels.get('target')}"
            )
            st.markdown("**Falsifiers as written**")
            for item in entry.get("falsifiers", []):
                st.markdown(f"- {item}")

        with st.form("close"):
            a, b = st.columns(2)
            outcome = a.selectbox("Outcome", journal.OUTCOMES)
            exit_price = b.number_input(
                "Exit price", min_value=0.0, value=0.0, format="%.4f"
            )
            note = st.text_area(
                "What actually happened",
                placeholder=(
                    "Whether the thesis was right, and whether it was right for "
                    "the reason given. Those come apart more often than anyone likes."
                ),
            )
            closing = st.form_submit_button("Close", disabled=blocked)

        if closing:
            is_position = (entry or {}).get("direction") in ("long", "short")
            if is_position and not exit_price:
                st.error(
                    "**No exit price.** Closing a position without one means "
                    "realised R is never computed and this call is excluded "
                    "from every performance figure permanently. Enter the fill."
                )
            else:
                result = ui.guard(
                    lambda: journal.close(
                        chosen,
                        outcome=outcome,
                        exit_price=exit_price or None,
                        note=note,
                    )
                )
                if result:
                    refresh()
                    realised = (result.get("outcome") or {}).get("realised_r")
                    st.success(
                        f"Closed **{result['id']}** — "
                        + (f"{realised}R realised" if realised is not None else "no R computed")
                    )
                    if not note.strip():
                        st.warning(
                            "No note recorded. Whether this worked for the "
                            "stated reason can no longer be reconstructed."
                        )

# --- Browse -----------------------------------------------------------------
with browse_tab:
    a, b = st.columns(2)
    ticker_filter = a.text_input("Ticker", key="browse_ticker").strip().upper()
    status_filter = b.selectbox("Status", ["", "open", "closed"])

    found = ui.guard(
        lambda: journal.search(
            ticker=ticker_filter or None, status=status_filter or None, limit=500
        )
    )

    if found is not None:
        rows = [
            {
                "id": t["id"],
                "ticker": t["ticker"],
                "status": t["status"],
                "direction": t.get("direction"),
                "horizon": t.get("horizon"),
                "conviction": t.get("conviction"),
                "opened": (t.get("created_at") or "")[:10],
                "closed": (t.get("closed_at") or "")[:10],
                "outcome": (t.get("outcome") or {}).get("result"),
                "realised_r": (t.get("outcome") or {}).get("realised_r"),
            }
            for t in found
        ]
        ui.table(rows, empty="No theses matched.")

        if ticker_filter and rows:
            st.info(
                f"The desk has looked at {ticker_filter} before. Read this "
                f"history before forming a new view — a second thesis that "
                f"contradicts the first without acknowledging it is how a desk "
                f"drifts."
            )
