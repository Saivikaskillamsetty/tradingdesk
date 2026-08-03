"""The correctness gate.

These tests exist because of a specific, silent failure mode: US-GAAP revenue
concepts drift per company, so reading a single hardcoded concept returns a
figure that is years out of date without any error. Apple's `Revenues` series
stops in 2018 at $62.9B; its real FY2025 revenue is $416.2B. An agent handed
the former builds a confident and completely wrong thesis.

No agent work should ship while any test here fails.

These hit the live SEC API (cached locally after the first run). Values are
pinned to filed 10-Ks and only change if a company restates.
"""

from __future__ import annotations

import pytest

from desk_mcp.edgar import facts
from desk_mcp.edgar.client import EdgarError

pytestmark = pytest.mark.network

BILLION = 1e9

# (ticker, expected revenue $B, fiscal year, period end, expected concept)
REVENUE_GOLDENS = [
    ("AAPL", 416.16, 2025, "2025-09-27",
     "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("NVDA", 215.9, 2026, "2026-01-25", "Revenues"),
    ("MSFT", 331.8, 2026, "2026-06-30",
     "RevenueFromContractWithCustomerExcludingAssessedTax"),
]


@pytest.mark.parametrize(
    "ticker,expected_b,fy,period_end,concept", REVENUE_GOLDENS
)
def test_revenue_matches_filed_10k(ticker, expected_b, fy, period_end, concept):
    fact = facts.latest(ticker, "revenue")

    assert fact.value / BILLION == pytest.approx(expected_b, rel=0.01)
    assert fact.fiscal_year == fy
    assert fact.period_end == period_end
    assert fact.form.startswith("10-K")


@pytest.mark.parametrize(
    "ticker,expected_b,fy,period_end,concept", REVENUE_GOLDENS
)
def test_resolver_picks_the_live_concept(
    ticker, expected_b, fy, period_end, concept
):
    """Each company uses a different concept; the resolver must find it.

    Hardcoding any one of these would break the other two.
    """
    assert facts.latest(ticker, "revenue").concept == concept


def test_apple_does_not_return_the_stale_2018_figure():
    """The specific regression this whole module defends against."""
    fact = facts.latest("AAPL", "revenue")

    assert fact.value / BILLION > 300, (
        f"got ${fact.value / BILLION:.1f}B via concept {fact.concept!r} "
        f"(period {fact.period_end}) -- this is the stale-concept bug"
    )
    assert not fact.period_end.startswith("2018")


def test_nvidia_would_break_a_hardcoded_apple_concept():
    """NVDA reports under `Revenues`, the concept Apple abandoned.

    Asserts the two companies genuinely disagree, so the test above is not
    passing by coincidence.
    """
    aapl = facts.latest("AAPL", "revenue").concept
    nvda = facts.latest("NVDA", "revenue").concept
    assert aapl != nvda


class TestProvenance:
    """Every value must be auditable back to a filing."""

    def test_all_provenance_fields_populated(self):
        fact = facts.latest("AAPL", "revenue")

        assert fact.concept
        assert fact.accession, "must cite the filing accession number"
        assert fact.filed, "must state when it was filed"
        assert fact.period_end
        assert fact.unit == "USD"

    def test_accession_is_a_real_sec_number(self):
        # Format: 0000320193-25-000079
        accession = facts.latest("AAPL", "revenue").accession
        parts = accession.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 10 and parts[0].isdigit()


class TestFiscalYearDerivation:
    """EDGAR's `fy` field describes the filing, not the period covered."""

    def test_fiscal_year_tracks_the_period_not_the_filing(self):
        history = {f.period_end: f for f in facts.series("AAPL", "revenue", limit=5)}
        fy2021 = history["2021-09-25"]

        assert fy2021.fiscal_year == 2021
        # EDGAR labels this row with the year of the 10-K that restated it.
        assert fy2021.reported_in_fy != 2021

    def test_january_year_end_belongs_to_ending_year(self):
        """NVIDIA's year ending Jan 2026 is FY2026, per NVIDIA."""
        assert facts.latest("NVDA", "revenue").fiscal_year == 2026


class TestStaleness:
    """Old data must raise, never be returned quietly."""

    def test_raises_when_beyond_max_age(self):
        with pytest.raises(facts.StaleDataError, match="days old"):
            facts.latest("AAPL", "revenue", max_age_days=1)

    def test_error_names_the_period_and_concept(self):
        with pytest.raises(facts.StaleDataError) as exc:
            facts.latest("AAPL", "revenue", max_age_days=1)

        message = str(exc.value)
        assert "AAPL" in message and "revenue" in message
        assert "concept" in message.lower()

    def test_passes_when_age_allowed(self):
        assert facts.latest("AAPL", "revenue", max_age_days=None).value > 0


class TestErrorHandling:
    def test_unknown_ticker_raises(self):
        with pytest.raises(EdgarError, match="no CIK found"):
            facts.latest("ZZZZNOTAREALTICKER", "revenue")

    def test_unknown_line_item_lists_valid_options(self):
        with pytest.raises(KeyError, match="revenue"):
            facts.latest("AAPL", "ebitda")


class TestStatementIntegrity:
    """Cross-line-item arithmetic must hold, or a concept is mismatched."""

    def test_gross_profit_reconciles(self):
        s = facts.statement("AAPL")["facts"]
        revenue = s["revenue"]["value"]
        cogs = s["cost_of_revenue"]["value"]
        gross = s["gross_profit"]["value"]

        assert revenue - cogs == pytest.approx(gross, rel=0.01)

    def test_balance_sheet_balances(self):
        s = facts.statement("AAPL")["facts"]
        assets = s["total_assets"]["value"]
        liabilities = s["total_liabilities"]["value"]
        equity = s["stockholders_equity"]["value"]

        assert assets == pytest.approx(liabilities + equity, rel=0.01)

    def test_all_line_items_share_the_same_period(self):
        """Mixing periods across a statement produces nonsense ratios."""
        s = facts.statement("AAPL")["facts"]
        ends = {f["period_end"] for f in s.values()}

        assert len(ends) == 1, f"line items span multiple periods: {sorted(ends)}"

    def test_apple_statement_is_complete(self):
        result = facts.statement("AAPL")
        assert result["unavailable"] == {}

    def test_statement_identifies_the_company(self):
        result = facts.statement("AAPL")
        assert "Apple" in result["company_name"]
        assert result["cik"] == 320193

    def test_unknown_ticker_raises_rather_than_returning_empty(self):
        """A bad ticker must not look like a company that reports nothing.

        `statement` tolerates individual missing line items, which previously
        meant an unknown ticker returned a successful-looking result with an
        empty `facts` block and one "no CIK found" note per line item.
        """
        with pytest.raises(EdgarError, match="no CIK found"):
            facts.statement("ZZZZNOTAREALTICKER")
