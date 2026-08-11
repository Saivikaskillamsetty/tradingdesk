"""Ownership data parsing, tested offline against filed shapes.

The traps here are quiet ones. A 13F that reports the same issuer on twelve
manager lines becomes a position a twelfth of its real size if the table is
read row-wise. A holding marked down by the market looks exactly like a sale
if changes are computed from values. A statutory amount band turned into a
midpoint is a figure nobody filed.
"""

from __future__ import annotations

import os

import pytest

from desk_mcp import smartmoney as sm

needs_network = pytest.mark.skipif(
    os.environ.get("DESK_SKIP_NETWORK_TESTS", "").strip().lower() in ("1", "true"),
    reason="DESK_SKIP_NETWORK_TESTS is set",
)


def line(issuer="ALLY FINL INC", cusip="02005N100", value=100.0, shares=10.0, put_call=None):
    return {
        "issuer": issuer,
        "cusip": cusip,
        "class": "COM",
        "value": value,
        "shares": shares,
        "put_call": put_call,
    }


class TestAggregation:
    def test_the_same_issuer_on_several_lines_becomes_one_position(self):
        """Berkshire files Apple once per sub-adviser; it is one holding."""
        merged = sm._aggregate([line(value=100.0, shares=10.0)] * 3)

        assert len(merged) == 1
        position = next(iter(merged.values()))
        assert position["shares"] == pytest.approx(30.0)
        assert position["value"] == pytest.approx(300.0)
        assert position["reported_lines"] == 3

    def test_puts_are_not_summed_into_the_stock(self):
        """A put on a name is the opposite position from holding it."""
        merged = sm._aggregate([line(), line(put_call="Put")])

        assert len(merged) == 2

    def test_different_share_classes_stay_separate(self):
        merged = sm._aggregate(
            [line(cusip="02079K305"), line(cusip="02079K107")]
        )

        assert len(merged) == 2

    def test_implied_price_exposes_a_filing_reported_in_thousands(self):
        """Pre-2023 filings reported value in thousands; the ratio shows it."""
        merged = sm._aggregate([line(value=500.0, shares=1000.0)])
        position = sm._position(next(iter(merged.values())), portfolio_value=500.0)

        assert position["implied_price_per_share"] == pytest.approx(0.5)

    def test_portfolio_weight_is_a_share_of_the_reported_book(self):
        merged = sm._aggregate([line(value=250.0, shares=10.0)])
        position = sm._position(next(iter(merged.values())), portfolio_value=1000.0)

        assert position["portfolio_weight_pct"] == pytest.approx(25.0)

    def test_a_position_with_no_shares_has_no_implied_price(self):
        merged = sm._aggregate([line(shares=0.0)])
        position = sm._position(next(iter(merged.values())), portfolio_value=100.0)

        assert position["implied_price_per_share"] is None


class TestQuarterOverQuarter:
    def meta(self):
        return {"period": "2025-12-31", "filed": "2026-02-17", "accession": "x"}

    def test_a_new_cusip_is_an_opened_position(self):
        diff = sm._diff(
            sm._aggregate([line(issuer="ALPHABET", cusip="02079K305")]),
            sm._aggregate([]),
            self.meta(),
        )

        assert [r["issuer"] for r in diff["opened"]] == ["ALPHABET"]

    def test_a_missing_cusip_is_an_exit(self):
        diff = sm._diff(
            sm._aggregate([]),
            sm._aggregate([line(issuer="VISA")]),
            self.meta(),
        )

        assert [r["issuer"] for r in diff["exited"]] == ["VISA"]

    def test_a_mark_down_is_not_reported_as_a_sale(self):
        """The whole reason changes are computed from share counts."""
        diff = sm._diff(
            sm._aggregate([line(value=50.0, shares=10.0)]),
            sm._aggregate([line(value=100.0, shares=10.0)]),
            self.meta(),
        )

        assert diff["reduced"] == []
        assert diff["increased"] == []

    def test_added_shares_are_reported_with_a_percentage(self):
        diff = sm._diff(
            sm._aggregate([line(shares=15.0)]),
            sm._aggregate([line(shares=10.0)]),
            self.meta(),
        )

        assert diff["increased"][0]["share_change"] == pytest.approx(5.0)
        assert diff["increased"][0]["share_change_pct"] == pytest.approx(50.0)

    def test_sold_shares_are_reported_as_a_reduction(self):
        diff = sm._diff(
            sm._aggregate([line(shares=4.0)]),
            sm._aggregate([line(shares=10.0)]),
            self.meta(),
        )

        assert diff["reduced"][0]["share_change"] == pytest.approx(-6.0)

    def test_the_comparison_period_is_carried_through(self):
        diff = sm._diff(sm._aggregate([]), sm._aggregate([]), self.meta())

        assert diff["compared_against"]["period"] == "2025-12-31"


# A PTR table as pypdf lays it out, including the run-together dates and the
# NUL-padded header glyphs the real filings produce.
PTR_TEXT = """
Name: Hon. Mark Alford
ID Owner Asset Transaction
Type
Date Notification
Date
Amount
Amazon.com, Inc. - Common Stock
(AMZN) [ST]
S (partial) 03/16/202603/16/2026$1,001 - $15,000
Apple Inc. - Common Stock (AAPL)
[ST]
P 03/17/202603/18/2026$15,001 - $50,000
NVIDIA Corporation (NVDA) [ST]
S 01/05/202602/20/2026$50,001 - $100,000
"""


class TestCongressParsing:
    def test_reads_every_transaction_row(self):
        assert len(sm._parse_ptr(PTR_TEXT)) == 3

    def test_extracts_the_ticker(self):
        assert [t["ticker"] for t in sm._parse_ptr(PTR_TEXT)] == [
            "AMZN",
            "AAPL",
            "NVDA",
        ]

    def test_separates_purchases_from_sales(self):
        meanings = [t["transaction_meaning"] for t in sm._parse_ptr(PTR_TEXT)]

        assert meanings == ["partial sale", "purchase", "sale"]

    def test_amounts_stay_ranges(self):
        """A midpoint would be a figure nobody filed."""
        assert sm._parse_ptr(PTR_TEXT)[0]["amount_range"] == "$1,001 - $15,000"

    def test_run_together_dates_are_split(self):
        trade = sm._parse_ptr(PTR_TEXT)[2]

        assert trade["transaction_date"] == "01/05/2026"
        assert trade["notification_date"] == "02/20/2026"

    def test_transaction_date_can_long_precede_disclosure(self):
        """45 days is the legal window and late filing is common."""
        trade = sm._parse_ptr(PTR_TEXT)[2]

        assert trade["transaction_date"] != trade["notification_date"]

    def test_a_scan_yields_nothing_rather_than_a_guess(self):
        assert sm._parse_ptr("scanned image, no extractable text") == []


class TestRefusals:
    def test_an_empty_institution_is_refused(self):
        with pytest.raises(sm.SmartMoneyError, match="name or CIK is required"):
            sm.resolve_institution("   ")

    def test_zero_quarters_is_refused(self):
        with pytest.raises(sm.SmartMoneyError, match="at least 1"):
            sm.holdings("berkshire hathaway", quarters=0)


@pytest.mark.network
@needs_network
class TestAgainstLiveEdgar:
    """Requires network. EDGAR needs no key."""

    def test_resolves_a_manager_by_name(self):
        filer = sm.resolve_institution("berkshire hathaway")

        assert filer["cik"] == 1067983

    def test_an_unknown_manager_is_refused_rather_than_guessed(self):
        with pytest.raises(sm.SmartMoneyError, match="no 13F filer"):
            sm.resolve_institution("definitely not a real fund xyzzy")

    def test_reads_a_real_thirteen_f_book(self):
        book = sm.holdings("berkshire hathaway")

        assert book["position_count"] > 5
        assert book["portfolio_value_usd"] > 0
        assert book["period"]

    def test_positions_sum_to_the_reported_portfolio(self):
        book = sm.holdings("berkshire hathaway")
        total = sum(p["value_usd"] for p in book["positions"])

        assert total == pytest.approx(book["portfolio_value_usd"], rel=1e-6)

    def test_a_current_filing_reports_value_in_dollars(self):
        """Implied price per share is the check; sub-dollar means thousands."""
        book = sm.holdings("berkshire hathaway")
        largest = book["positions"][0]

        assert largest["implied_price_per_share"] > 1.0

    def test_the_reporting_lag_is_stated(self):
        assert sm.holdings("berkshire hathaway")["reporting_lag_days"] >= 0


@pytest.mark.network
@needs_network
class TestAgainstLiveHouseClerk:
    """Requires network. House disclosures need no key."""

    def test_the_annual_index_carries_ptr_filings(self):
        index = sm._house_index(2026)

        assert len(index) > 50
        assert all(row["doc_id"] for row in index)

    def test_parses_real_disclosures(self):
        result = sm.congress_trades(max_reports=3)

        assert result["ptr_filings_read"] == 3
        assert result["ptr_filings_total"] > 3

    def test_unreadable_scans_are_reported_not_dropped(self):
        """A member whose filing cannot be read is not one who did not trade."""
        result = sm.congress_trades(max_reports=12)

        assert "unreadable_filings" in result
        assert result["trades"] or result["unreadable_filings"]
