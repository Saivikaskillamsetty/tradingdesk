"""The desk's self-check, tested against a temporary theses directory.

Every failure this module looks for is one that raises nothing. The tests
therefore care most that a broken book is reported as broken -- a health check
that reads reassuringly while the journal is missing exit prices is worse than
no health check, because it launders the gap into confidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from desk_mcp import health, journal


@pytest.fixture(autouse=True)
def theses_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    """Start from a known credential state rather than the developer's shell."""
    for name in health.CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


def position(**overrides):
    payload = {
        "ticker": "NVDA",
        "thesis": "Trend intact.",
        "falsifiers": ["close below $150"],
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
        "shares": 50,
        "dollar_risk": 500.0,
    }
    payload.update(overrides)
    return journal.record(**payload)


def age(theses_dir, entry_id: str, days: int):
    """Backdate a thesis so staleness can be tested without waiting."""
    import json

    path = theses_dir / f"{entry_id}.json"
    payload = json.loads(path.read_text())
    payload["created_at"] = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload))


def findings_text(report):
    return " ".join(f["finding"] for f in report["findings"])


class TestCleanBook:
    """A fully configured desk, so status reflects the book and nothing else."""

    @pytest.fixture(autouse=True)
    def all_credentials(self, monkeypatch):
        for name in health.CREDENTIALS:
            monkeypatch.setenv(name, "present-for-this-test")

    def test_an_empty_desk_is_healthy(self):
        report = health.desk_health()

        assert report["journal"]["total_theses"] == 0
        assert report["status"] == "ok"

    def test_a_complete_call_raises_nothing(self):
        journal.close(
            position()["id"], outcome="target_hit", exit_price=130.0, note="Worked."
        )

        assert health.desk_health()["status"] == "ok"

    def test_a_missing_key_alone_is_a_warning_not_an_error(self, monkeypatch):
        """Configuration gaps must not read as a corrupted book."""
        monkeypatch.delenv("FRED_API_KEY", raising=False)

        assert health.desk_health()["status"] == "warning"


class TestSilentFailures:
    def test_an_unparseable_thesis_file_is_an_error(self, theses_dir):
        """`list_theses` skips these silently, so the book is quietly short."""
        position()
        (theses_dir / "corrupt.json").write_text("{not json")

        report = health.desk_health()

        assert report["status"] == "error"
        assert len(report["journal"]["unreadable_files"]) == 1
        assert "quietly incomplete" in findings_text(report)

    def test_a_close_without_an_exit_price_is_an_error(self):
        journal.close(position()["id"], outcome="expired", note="Gave up on it.")

        report = health.desk_health()

        assert report["status"] == "error"
        assert len(report["journal"]["closed_without_exit_price"]) == 1

    def test_an_open_position_without_sizing_understates_heat(self):
        position(dollar_risk=None)

        report = health.desk_health()

        assert report["status"] == "error"
        assert len(report["journal"]["open_positions_without_sizing"]) == 1
        assert "heat" in findings_text(report).lower()

    def test_a_watch_call_needs_no_sizing(self):
        journal.record(
            "NFLX", thesis="Watching.", falsifiers=["reclaim $80"], direction="watch"
        )

        assert health.desk_health()["journal"]["open_positions_without_sizing"] == []

    def test_a_watch_call_closed_without_a_price_is_not_an_error(self):
        """Watch calls have no exit price by design."""
        watch = journal.record(
            "NFLX", thesis="Watching.", falsifiers=["reclaim $80"], direction="watch"
        )
        journal.close(watch["id"], outcome="expired", note="Never triggered.")

        assert health.desk_health()["journal"]["closed_without_exit_price"] == []

    def test_a_missing_close_note_is_a_warning(self):
        journal.close(position()["id"], outcome="target_hit", exit_price=130.0)

        report = health.desk_health()

        assert report["status"] == "warning"
        assert len(report["journal"]["closed_without_note"]) == 1


class TestStaleness:
    def test_a_swing_call_open_past_its_horizon_is_flagged(self, theses_dir):
        entry = position(horizon="swing")
        age(theses_dir, entry["id"], days=120)

        stale = health.desk_health()["journal"]["stale_open_theses"]

        assert [s["ticker"] for s in stale] == ["NVDA"]
        assert stale[0]["stale_after_days"] == health.STALE_AFTER_DAYS["swing"]

    def test_a_long_term_call_gets_a_longer_leash(self, theses_dir):
        entry = position(horizon="long_term")
        age(theses_dir, entry["id"], days=120)

        assert health.desk_health()["journal"]["stale_open_theses"] == []

    def test_a_fresh_call_is_not_stale(self):
        position(horizon="swing")

        assert health.desk_health()["journal"]["stale_open_theses"] == []

    def test_a_closed_call_cannot_go_stale(self, theses_dir):
        entry = position(horizon="swing")
        age(theses_dir, entry["id"], days=400)
        journal.close(entry["id"], outcome="expired", exit_price=95.0, note="Done.")

        assert health.desk_health()["journal"]["stale_open_theses"] == []

    def test_the_oldest_is_reported_first(self, theses_dir):
        older = position(ticker="AMD", horizon="swing")
        newer = position(ticker="INTC", horizon="swing")
        age(theses_dir, older["id"], days=300)
        age(theses_dir, newer["id"], days=90)

        stale = health.desk_health()["journal"]["stale_open_theses"]

        assert [s["ticker"] for s in stale] == ["AMD", "INTC"]


class TestCredentials:
    def test_missing_keys_are_reported_with_what_they_block(self):
        report = health.desk_health()
        blocked = {m["variable"] for m in report["credentials"]["missing"]}

        assert "ALPACA_API_KEY" in blocked
        assert report["status"] in ("warning", "error")

    def test_a_present_key_is_listed_without_its_value(self, monkeypatch):
        """Presence only. A health report is not a place to leak a secret."""
        monkeypatch.setenv("FRED_API_KEY", "super-secret-value")

        report = health.desk_health()

        assert "FRED_API_KEY" in report["credentials"]["present"]
        assert "super-secret-value" not in str(report)

    def test_keyless_sources_are_named_so_they_are_not_chased(self):
        assert "SEC EDGAR" in health.desk_health()["credentials"]["keyless_sources"]


class TestReporting:
    def test_errors_outrank_warnings_in_the_status(self):
        journal.close(position()["id"], outcome="expired")  # error: no exit price

        assert health.desk_health()["status"] == "error"

    def test_limitations_say_what_was_not_checked(self):
        report = health.desk_health()
        text = " ".join(report["limitations"])

        assert "presence, not validity" in text
        assert "reconcile_positions" in text

    def test_the_check_is_timestamped(self):
        assert health.desk_health()["checked_at"]
