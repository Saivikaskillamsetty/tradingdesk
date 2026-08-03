"""Metric arithmetic, tested on synthetic inputs.

These run offline. The point is the maths, not the data -- if a ratio is
computed wrongly here, every agent downstream inherits the error silently.
"""

from __future__ import annotations

import pytest

from desk_mcp import metrics
from desk_mcp.edgar.client import EdgarError
from desk_mcp.edgar.facts import Fact


def fact(line_item: str, value: float, *, end: str = "2025-12-31", fy: int = 2025):
    return Fact(
        line_item=line_item,
        label=line_item,
        value=value,
        unit="USD",
        concept=f"Test{line_item}",
        period_start="2025-01-01",
        period_end=end,
        fiscal_year=fy,
        fiscal_period="FY",
        form="10-K",
        accession="0000000000-25-000001",
        filed="2026-01-15",
        reported_in_fy=fy,
        reported_in_fp="FY",
    )


def statement(**line_items: float) -> dict:
    facts = {k: fact(k, v).to_dict() for k, v in line_items.items()}
    return {
        "facts": facts,
        "period_end_common": "2025-12-31",
    }


class TestSafeDivision:
    """A zero denominator means "meaningless", not "infinite"."""

    def test_zero_denominator_yields_none(self):
        assert metrics._safe_div(100, 0) is None

    def test_negative_denominator_still_divides(self):
        # Negative equity is meaningful as a signal; the caller flags it.
        assert metrics._safe_div(100, -50) == -2.0

    def test_normal_division(self):
        assert metrics._safe_div(50, 200) == 0.25


class TestProfitability:
    def test_margins(self):
        s = statement(
            revenue=1000, gross_profit=400, operating_income=250, net_income=200
        )
        by_key = {m.key: m.value for m in metrics.profitability(s)}

        assert by_key["gross_margin"] == pytest.approx(40.0)
        assert by_key["operating_margin"] == pytest.approx(25.0)
        assert by_key["net_margin"] == pytest.approx(20.0)

    def test_loss_making_company_reports_negative_margin(self):
        s = statement(revenue=1000, net_income=-300)
        by_key = {m.key: m.value for m in metrics.profitability(s)}

        assert by_key["net_margin"] == pytest.approx(-30.0)

    def test_zero_revenue_produces_no_margins(self):
        assert metrics.profitability(statement(revenue=0, net_income=50)) == []

    def test_missing_line_item_is_skipped_not_guessed(self):
        s = statement(revenue=1000, net_income=200)
        keys = {m.key for m in metrics.profitability(s)}

        assert "net_margin" in keys
        assert "gross_margin" not in keys


class TestCashQuality:
    def test_free_cash_flow_subtracts_capex_magnitude(self):
        """Capex sign convention varies; magnitude is what matters."""
        positive = statement(revenue=1000, operating_cash_flow=500, capex=120)
        negative = statement(revenue=1000, operating_cash_flow=500, capex=-120)

        def fcf(s):
            return next(m.value for m in metrics.cash_quality(s) if m.key == "free_cash_flow")

        assert fcf(positive) == pytest.approx(380)
        assert fcf(negative) == pytest.approx(380)

    def test_cash_conversion(self):
        s = statement(revenue=1000, operating_cash_flow=180, net_income=200)
        by_key = {m.key: m.value for m in metrics.cash_quality(s)}

        assert by_key["cash_conversion"] == pytest.approx(90.0)

    def test_conversion_omitted_when_loss_making(self):
        """Conversion against negative earnings is not interpretable."""
        s = statement(revenue=1000, operating_cash_flow=180, net_income=-200)
        by_key = {m.key: m.value for m in metrics.cash_quality(s)}

        assert "cash_conversion" not in by_key


class TestBalanceSheet:
    def test_net_debt_negative_means_net_cash(self):
        s = statement(long_term_debt=100, cash_and_equivalents=250)
        by_key = {m.key: m.value for m in metrics.balance_sheet(s)}

        assert by_key["net_debt"] == pytest.approx(-150)

    def test_negative_equity_is_flagged(self):
        s = statement(long_term_debt=100, stockholders_equity=-50)
        metric = next(
            m for m in metrics.balance_sheet(s) if m.key == "debt_to_equity"
        )

        assert "meaningless" in metric.note


class TestReturns:
    def test_uses_average_balance_when_prior_available(self):
        current = statement(net_income=200, stockholders_equity=1200, total_assets=2000)
        prior = statement(stockholders_equity=800, total_assets=1600)

        roe = next(m for m in metrics.returns(current, prior) if m.key == "roe")

        # 200 / ((800 + 1200) / 2) = 20%
        assert roe.value == pytest.approx(20.0)
        assert "average" in roe.note

    def test_falls_back_to_closing_balance_and_says_so(self):
        current = statement(net_income=200, stockholders_equity=1200, total_assets=2000)

        roe = next(m for m in metrics.returns(current, None) if m.key == "roe")

        assert roe.value == pytest.approx(200 / 1200 * 100)
        assert "closing balance only" in roe.note

    def test_average_and_closing_differ_for_a_growing_company(self):
        """The reason average balances matter at all."""
        current = statement(net_income=200, stockholders_equity=1200, total_assets=2000)
        prior = statement(stockholders_equity=800, total_assets=1600)

        with_prior = next(m for m in metrics.returns(current, prior) if m.key == "roe")
        without = next(m for m in metrics.returns(current, None) if m.key == "roe")

        assert with_prior.value > without.value


class TestGrowth:
    def test_year_over_year(self):
        series = [fact("revenue", 100, fy=2024), fact("revenue", 125, fy=2025)]
        by_key = {m.key: m.value for m in metrics.growth(series, "Revenue")}

        assert by_key["revenue_yoy"] == pytest.approx(25.0)

    def test_cagr_over_multiple_years(self):
        # 100 -> 800 over 3 years is exactly 100% a year.
        series = [fact("revenue", v, fy=y) for v, y in
                  ((100, 2022), (200, 2023), (400, 2024), (800, 2025))]
        by_key = {m.key: m.value for m in metrics.growth(series, "Revenue")}

        assert by_key["revenue_cagr"] == pytest.approx(100.0)

    def test_cagr_omitted_from_negative_base(self):
        """A compound rate from a loss has no real meaning."""
        series = [fact("net_income", v, fy=y) for v, y in
                  ((-50, 2023), (20, 2024), (60, 2025))]
        by_key = {m.key: m.value for m in metrics.growth(series, "Net income")}

        assert "net_income_cagr" not in by_key
        assert "net_income_yoy" in by_key

    def test_recovery_from_a_loss_is_reported_as_positive_growth(self):
        series = [fact("net_income", -100, fy=2024), fact("net_income", 50, fy=2025)]
        by_key = {m.key: m.value for m in metrics.growth(series, "Net income")}

        # -100 -> +50 is a 150-point swing on a base magnitude of 100.
        assert by_key["net_income_yoy"] == pytest.approx(150.0)

    def test_single_period_yields_nothing(self):
        assert metrics.growth([fact("revenue", 100)], "Revenue") == []


class TestPeriodConsistency:
    def test_refuses_to_mix_periods(self):
        """Ratios across mismatched periods are nonsense, so they must raise."""
        s = {
            "facts": {
                "revenue": fact("revenue", 1000, end="2025-12-31").to_dict(),
                "net_income": fact("net_income", 200, end="2024-12-31").to_dict(),
            }
        }

        with pytest.raises(EdgarError, match="multiple periods"):
            metrics._common_period(s)

    def test_accepts_a_single_period(self):
        s = {
            "facts": {
                "revenue": fact("revenue", 1000, end="2025-12-31").to_dict(),
                "net_income": fact("net_income", 200, end="2025-12-31").to_dict(),
            }
        }

        assert metrics._common_period(s) == "2025-12-31"


class TestProvenance:
    def test_every_metric_names_its_inputs(self):
        s = statement(revenue=1000, gross_profit=400)
        metric = next(
            m for m in metrics.profitability(s) if m.key == "gross_margin"
        )

        assert set(metric.inputs) == {"gross_profit", "revenue"}

    def test_metric_carries_its_period(self):
        s = statement(revenue=1000, gross_profit=400)
        assert all(m.period_end == "2025-12-31" for m in metrics.profitability(s))


@pytest.mark.network
class TestAgainstRealFilings:
    """Sanity checks that the pipeline holds together on live data."""

    def test_apple_margins_are_plausible(self):
        result = metrics.analyse("AAPL")
        by_key = {m["key"]: m["value"] for m in result["metrics"]}

        assert 35 < by_key["gross_margin"] < 55
        assert 15 < by_key["net_margin"] < 35

    def test_nvidia_holds_net_cash(self):
        result = metrics.analyse("NVDA")
        by_key = {m["key"]: m["value"] for m in result["metrics"]}

        assert by_key["net_debt"] < 0

    def test_every_metric_is_stamped_with_one_period(self):
        result = metrics.analyse("AAPL")
        periods = {m["period_end"] for m in result["metrics"]}

        assert len(periods) == 1
