"""Who else is in the name: institutions, insiders, and Congress.

Three populations with three different lags. The page keeps them apart on
purpose — the most common way this data misleads is by being read as one
signal in the present tense.
"""

from __future__ import annotations

import streamlit as st

from desk_mcp import smartmoney
from desk_mcp.edgar import filings
from desk_ui import charts
from desk_ui import common as ui

ui.page("Smart money", "🏛️", "Disclosed ownership, and how late each disclosure is.")
ui.sidebar_note()


@st.cache_data(ttl=ui.TTL_SLOW)
def holdings(institution: str) -> dict:
    return smartmoney.holdings(institution)


@st.cache_data(ttl=ui.TTL_SLOW)
def congress(ticker: str | None, member: str | None, reports: int) -> dict:
    return smartmoney.congress_trades(
        ticker=ticker or None, member=member or None, max_reports=reports
    )


@st.cache_data(ttl=ui.TTL_SLOW)
def activity(reports: int) -> dict:
    return smartmoney.congress_activity_by_ticker(max_reports=reports)


@st.cache_data(ttl=ui.TTL_SLOW)
def insiders(ticker: str) -> dict:
    return filings.insider_activity(ticker)


institutions, congress_tab, insider_tab = st.tabs(
    ["Institutions (13F)", "Congress", "Insiders (Form 4)"]
)

# --- 13F --------------------------------------------------------------------
with institutions:
    name = st.text_input(
        "Manager name or CIK", placeholder="berkshire hathaway"
    ).strip()

    if not name:
        st.caption(
            "Names resolve through EDGAR. An ambiguous name returns the "
            "candidates rather than guessing which fund you meant."
        )
    else:
        book = ui.guard(lambda: holdings(name))
        if book:
            st.subheader(book["institution"])
            ui.metric_row(
                [
                    ("Period", book["period"], "Holdings as of this date"),
                    ("Filed", book["filed"], None),
                    ("Lag", f"{book['reporting_lag_days']} days", "How stale this is"),
                    ("Positions", book["position_count"], None),
                    ("Reported book", ui.money(book["portfolio_value_usd"]), None),
                ]
            )

            st.warning(
                f"**This is a photograph of {book['period']}, developed "
                f"{book['reporting_lag_days']} days later.** The book has moved "
                f"since. Long US-listed equity only — no shorts, no cash, no "
                f"foreign listings."
            )

            ui.chart(
                charts.top_holdings(
                    book["positions"],
                    title=f"Largest positions as of {book['period']} · label is portfolio weight %",
                )
            )

            ui.table(
                book["positions"],
                {
                    "issuer": "Issuer",
                    "class": "Class",
                    "value_usd": "Value (USD)",
                    "shares": "Shares",
                    "portfolio_weight_pct": "Weight %",
                    "implied_price_per_share": "Implied $/share",
                    "reported_lines": "Filed lines",
                },
            )
            st.caption(
                "`Filed lines` above 1 means the manager reported that issuer on "
                "several sub-adviser lines; they are summed here into one "
                "position. `Implied $/share` is a sanity check — a sub-dollar "
                "figure means the filing reported value in thousands."
            )

            changes = book.get("changes")
            if changes:
                st.subheader(
                    f"Changes since {changes['compared_against']['period']}"
                )
                st.caption(changes["note"])

                ui.chart(
                    charts.position_changes(
                        changes, title="Change in share count since the prior filing"
                    )
                )

                opened, exited = st.columns(2)
                with opened:
                    st.markdown("**Opened**")
                    ui.table(changes["opened"], empty="Nothing new.")
                with exited:
                    st.markdown("**Exited**")
                    ui.table(changes["exited"], empty="Nothing sold out.")

                added, cut = st.columns(2)
                with added:
                    st.markdown("**Increased**")
                    ui.table(changes["increased"], empty="No adds.")
                with cut:
                    st.markdown("**Reduced**")
                    ui.table(changes["reduced"], empty="No trims.")

            ui.limitations(book["limitations"])

# --- Congress ---------------------------------------------------------------
with congress_tab:
    a, b, c = st.columns([1, 2, 1])
    symbol = a.text_input("Ticker", key="cg_ticker").strip().upper()
    member = b.text_input("Member name contains", key="cg_member").strip()
    reports = c.number_input("Reports to read", 5, 200, 40, step=5)

    st.caption(
        "Each report is a separate PDF download on first read, so raising this "
        "costs time. Cached permanently afterwards."
    )

    if st.button("Search disclosures"):
        result = ui.guard(lambda: congress(symbol, member, int(reports)))
        if result:
            st.caption(
                f"Read {result['ptr_filings_read']} of "
                f"{result['ptr_filings_total']} {result['year']} reports."
            )

            ui.table(
                result["trades"],
                {
                    "member": "Member",
                    "state_district": "District",
                    "ticker": "Ticker",
                    "transaction_meaning": "Type",
                    "transaction_date": "Traded",
                    "disclosed": "Disclosed",
                    "amount_range": "Amount (band)",
                    "source_url": "Filing",
                },
                empty="No transactions matched in the reports read.",
            )

            st.info(
                "**Amounts are the statutory bands members file.** There is no "
                "exact figure behind them, and a midpoint would be a number "
                "nobody reported."
            )

            if result["unreadable_filings"]:
                st.warning(
                    f"{len(result['unreadable_filings'])} filings could not be "
                    f"read — almost always scans rather than digital filings. "
                    f"A member whose filing cannot be parsed is not one who did "
                    f"not trade."
                )
                ui.table(
                    result["unreadable_filings"],
                    {"member": "Member", "filed": "Filed", "url": "Filing"},
                )

            ui.limitations(result["limitations"])

    st.divider()
    if st.button("Most-disclosed names"):
        ranked = ui.guard(lambda: activity(int(reports)))
        if ranked:
            ui.table(
                ranked["most_traded"],
                {
                    "ticker": "Ticker",
                    "purchases": "Buys",
                    "sales": "Sells",
                    "net_disclosures": "Net",
                    "distinct_members": "Members",
                },
            )
            st.caption(
                "Counts disclosures, not dollars. One member's $1M purchase and "
                "another's $1,001 purchase count the same."
            )

# --- Form 4 -----------------------------------------------------------------
with insider_tab:
    symbol = st.text_input("Ticker", key="in_ticker").strip().upper()
    if symbol:
        found = ui.guard(lambda: insiders(symbol))
        if found:
            st.metric("Recent Form 4 filings", found["form_4_count"])
            ui.table(
                found["filings"],
                {"form": "Form", "filed": "Filed", "period": "Period", "url": "Filing"},
                empty="No recent insider filings.",
            )
            st.info(found["note"])
            ui.limitations(
                [
                    "Filings and links only — transaction size, price and role "
                    "live inside each filing. Open one to read them.",
                    "Form 4 is the timeliest of the three sources here: two "
                    "business days, against 45 for the other two.",
                ]
            )
