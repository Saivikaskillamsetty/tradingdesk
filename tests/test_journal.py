"""Journal storage, tested against a temporary theses directory.

These run offline. What matters here is that a thesis cannot be recorded in a
shape that makes it unscoreable later -- a call with no falsifier, or a long
with no stop, is worse than no record at all because it still looks like one.
"""

from __future__ import annotations

import json

import pytest

from desk_mcp import journal
from desk_mcp.journal import JournalError


@pytest.fixture(autouse=True)
def theses_dir(tmp_path, monkeypatch):
    """Point the journal at a scratch directory for every test."""
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    return tmp_path


def watch(**overrides):
    payload = {
        "ticker": "nflx",
        "thesis": "Strong business, broken chart. Watchlist.",
        "falsifiers": ["reclaim of the 50-day at $76.80"],
    }
    payload.update(overrides)
    return journal.record(**payload)


def position(**overrides):
    payload = {
        "ticker": "NVDA",
        "thesis": "Trend intact, buying the 50-day.",
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


class TestRecording:
    def test_writes_a_readable_file(self, theses_dir):
        entry = watch()
        path = theses_dir / f"{entry['id']}.json"

        assert path.exists()
        assert json.loads(path.read_text())["ticker"] == "NFLX"

    def test_ticker_is_normalised(self):
        assert watch()["ticker"] == "NFLX"

    def test_id_carries_date_and_ticker(self):
        entry = watch()
        assert entry["ticker"] in entry["id"]

    def test_ids_are_unique_for_the_same_name_and_day(self):
        assert watch()["id"] != watch()["id"]

    def test_starts_open(self):
        assert watch()["status"] == "open"

    def test_risk_per_share_is_computed_not_supplied(self):
        assert position()["levels"]["risk_per_share"] == pytest.approx(10.0)


class TestRecordingRefusals:
    def test_a_thesis_without_falsifiers_is_refused(self):
        """A call nothing could disprove can only ever be rationalised."""
        with pytest.raises(JournalError, match="falsifier"):
            watch(falsifiers=[])

    def test_a_long_without_a_stop_is_refused(self):
        with pytest.raises(JournalError, match="entry and a stop"):
            journal.record(
                "NVDA",
                thesis="Buying it.",
                falsifiers=["close below $150"],
                direction="long",
                entry=100.0,
            )

    def test_a_watch_call_needs_no_levels(self):
        assert watch()["levels"]["entry"] is None

    def test_unknown_direction_is_refused(self):
        with pytest.raises(JournalError, match="direction"):
            watch(direction="sideways")

    def test_empty_thesis_is_refused(self):
        with pytest.raises(JournalError, match="thesis text"):
            watch(thesis="   ")

    def test_id_traversal_is_refused(self):
        with pytest.raises(JournalError, match="invalid thesis id"):
            journal.load("../../etc/passwd")


class TestSearch:
    def test_filters_by_ticker(self):
        watch()
        position()

        assert [t["ticker"] for t in journal.search(ticker="nvda")] == ["NVDA"]

    def test_filters_by_status(self):
        kept = position()
        journal.close(watch()["id"], outcome="expired")

        assert [t["id"] for t in journal.search(status="open")] == [kept["id"]]

    def test_empty_directory_returns_nothing(self):
        assert journal.search() == []

    def test_unparseable_file_is_skipped_not_fatal(self, theses_dir):
        watch()
        (theses_dir / "corrupt.json").write_text("{not json")

        assert len(journal.search()) == 1


class TestClosing:
    def test_records_outcome_and_timestamp(self):
        entry = journal.close(position()["id"], outcome="target_hit", exit_price=130.0)

        assert entry["status"] == "closed"
        assert entry["closed_at"] is not None
        assert entry["outcome"]["result"] == "target_hit"

    def test_realised_r_is_computed_from_entry_and_stop(self):
        # Entry 100, stop 90, exit 130 -> 30 points of gain on 10 of risk.
        entry = journal.close(position()["id"], outcome="target_hit", exit_price=130.0)

        assert entry["outcome"]["realised_r"] == pytest.approx(3.0)

    def test_a_stop_out_is_negative_one_r(self):
        entry = journal.close(position()["id"], outcome="stopped_out", exit_price=90.0)

        assert entry["outcome"]["realised_r"] == pytest.approx(-1.0)

    def test_short_direction_inverts_the_sign(self):
        short = journal.record(
            "TSLA",
            thesis="Fading the spike.",
            falsifiers=["close above $300"],
            direction="short",
            entry=100.0,
            stop=110.0,
        )
        entry = journal.close(short["id"], outcome="target_hit", exit_price=80.0)

        assert entry["outcome"]["realised_r"] == pytest.approx(2.0)

    def test_no_exit_price_leaves_r_unknown_rather_than_zero(self):
        entry = journal.close(position()["id"], outcome="expired")

        assert entry["outcome"]["realised_r"] is None

    def test_watch_calls_have_no_r(self):
        entry = journal.close(watch()["id"], outcome="expired", exit_price=50.0)

        assert entry["outcome"]["realised_r"] is None

    def test_closing_twice_is_refused(self):
        thesis_id = position()["id"]
        journal.close(thesis_id, outcome="target_hit", exit_price=130.0)

        with pytest.raises(JournalError, match="already closed"):
            journal.close(thesis_id, outcome="stopped_out")

    def test_unknown_outcome_is_refused(self):
        with pytest.raises(JournalError, match="outcome must be"):
            journal.close(position()["id"], outcome="went_well")

    def test_missing_thesis_is_refused(self):
        with pytest.raises(JournalError, match="no thesis"):
            journal.load("2026-01-01-AAPL-abcdef")


class TestOpenRisk:
    def test_sums_only_open_positions(self):
        position()
        position(ticker="AMD", dollar_risk=250.0)
        journal.close(position(ticker="INTC")["id"], outcome="stopped_out")

        exposure = journal.open_risk()

        assert exposure["open_positions"] == 2
        assert exposure["total_dollar_risk"] == pytest.approx(750.0)

    def test_watch_calls_carry_no_risk(self):
        watch()
        exposure = journal.open_risk()

        assert exposure["open_theses"] == 1
        assert exposure["open_positions"] == 0
        assert exposure["watch_only"] == 1
        assert exposure["total_dollar_risk"] == 0

    def test_a_position_recorded_without_sizing_is_not_counted(self):
        """Uncounted risk must not be silently treated as zero risk."""
        position(dollar_risk=None)

        assert journal.open_risk()["open_positions"] == 0

    def test_no_journal_means_no_exposure(self):
        assert journal.open_risk()["total_dollar_risk"] == 0
