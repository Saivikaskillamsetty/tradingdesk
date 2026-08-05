"""Macro change arithmetic, tested on synthetic observations.

These run offline. The awkward part of macro data is not fetching it, it is
comparing against the right earlier print: markets close for weekends and
holidays, monthly series publish once, and a naive calendar comparison
silently misses on all of them.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from desk_mcp import macro
from desk_mcp.macro import MacroError

needs_fred_key = pytest.mark.skipif(
    not os.environ.get("FRED_API_KEY", "").strip(),
    reason="FRED_API_KEY not set — macro is the desk's one optional credential",
)


def points(*pairs: tuple[str, float]) -> list[dict]:
    return [{"date": d, "value": v} for d, v in pairs]


class TestComparisonDate:
    def test_finds_the_exact_date_when_it_exists(self):
        series = points(("2026-01-01", 1.0), ("2026-02-01", 2.0))

        assert macro._value_on_or_before(series, date(2026, 2, 1))["value"] == 2.0

    def test_walks_back_to_the_last_real_print(self):
        """A Sunday comparison must land on Friday, not miss entirely."""
        series = points(("2026-08-01", 4.0), ("2026-08-03", 5.0))

        found = macro._value_on_or_before(series, date(2026, 8, 2))

        assert found["date"] == "2026-08-01"

    def test_returns_none_before_the_series_starts(self):
        series = points(("2026-08-01", 4.0))

        assert macro._value_on_or_before(series, date(2025, 1, 1)) is None


class TestChange:
    def test_absolute_and_percentage_change(self):
        series = points(("2026-01-05", 4.0), ("2026-08-03", 5.0))

        change = macro._change(series, days=210)

        assert change["from_value"] == 4.0
        assert change["change"] == pytest.approx(1.0)
        assert change["change_pct"] == pytest.approx(25.0)

    def test_a_fall_is_negative(self):
        series = points(("2026-01-05", 5.0), ("2026-08-03", 4.0))

        assert macro._change(series, days=210)["change"] == pytest.approx(-1.0)

    def test_no_history_yields_none_rather_than_zero(self):
        """No comparison point means unknown, which is not "unchanged"."""
        series = points(("2026-08-03", 5.0))

        assert macro._change(series, days=365) is None

    def test_zero_base_gives_no_percentage(self):
        series = points(("2026-01-05", 0.0), ("2026-08-03", 2.0))
        change = macro._change(series, days=210)

        assert change["change"] == pytest.approx(2.0)
        assert change["change_pct"] is None


class TestSeriesRegistry:
    def test_every_series_explains_how_to_read_it(self):
        assert all(s.reads_as for s in macro.SERIES)

    def test_every_series_has_a_fred_id(self):
        assert all(s.series_id for s in macro.SERIES)

    def test_keys_are_unique(self):
        keys = [s.key for s in macro.SERIES]

        assert len(keys) == len(set(keys))

    def test_unknown_series_is_refused_with_the_valid_list(self):
        with pytest.raises(MacroError, match="unknown series"):
            macro.series_reading("gold_price")


class TestMissingCredentials:
    def test_absent_api_key_says_where_to_get_one(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)

        with pytest.raises(MacroError, match="fredaccount.stlouisfed.org"):
            macro._api_key()

    def test_blank_api_key_is_treated_as_absent(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "   ")

        with pytest.raises(MacroError):
            macro._api_key()


class TestSnapshotResilience:
    def test_one_failing_series_does_not_take_the_snapshot_down(self, monkeypatch):
        def reading(key: str):
            if key == "vix":
                raise MacroError("VIXCLS: no usable observations returned")
            return {"key": key, "value": 1.0, "as_of": "2026-08-03"}

        monkeypatch.setattr(macro, "series_reading", reading)
        result = macro.snapshot(keys=["treasury_10y", "vix"])

        assert "treasury_10y" in result["readings"]
        assert "vix" in result["unavailable"]

    def test_total_failure_raises_rather_than_returning_an_empty_snapshot(
        self, monkeypatch
    ):
        def reading(key: str):
            raise MacroError("FRED_API_KEY is not set")

        monkeypatch.setattr(macro, "series_reading", reading)

        with pytest.raises(MacroError, match="no macro series"):
            macro.snapshot(keys=["treasury_10y"])


@pytest.mark.network
@needs_fred_key
class TestAgainstLiveFred:
    """Requires FRED_API_KEY.

    Skipped rather than failed when the key is absent. A suite that goes red
    for a known environmental reason teaches everyone to ignore red.
    """

    def test_ten_year_yield_is_a_plausible_rate(self):
        reading = macro.series_reading("treasury_10y")

        assert 0 < reading["value"] < 25
        assert reading["series_id"] == "DGS10"

    def test_snapshot_covers_every_series(self):
        result = macro.snapshot()

        assert len(result["readings"]) + len(result["unavailable"]) == len(
            macro.SERIES
        )
