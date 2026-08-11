"""Scoring of the closed book, tested against a temporary theses directory.

These run offline. What matters here is that the scoring cannot flatter the
desk: a call that produced no R must never be counted as a scratch, a watch
call must never enter the performance figures, and a sample too small to mean
anything must say so rather than quietly reporting a confident expectancy.
"""

from __future__ import annotations

import pytest

from desk_mcp import journal, postmortem
from desk_mcp.journal import JournalError
from desk_mcp.postmortem import PostmortemError


@pytest.fixture(autouse=True)
def theses_dir(tmp_path, monkeypatch):
    """Point the journal at a scratch directory for every test."""
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    return tmp_path


def record(**overrides):
    """A long thesis with entry 100, stop 90, target 130 — a planned 3R."""
    payload = {
        "ticker": "NVDA",
        "thesis": "Trend intact, buying the 50-day.",
        "falsifiers": ["close below $90"],
        "direction": "long",
        "horizon": "swing",
        "conviction": "medium",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
        "shares": 50,
        "dollar_risk": 500.0,
    }
    payload.update(overrides)
    return journal.record(**payload)


def closed(outcome="target_hit", exit_price=130.0, note="", **overrides):
    """Record and immediately close a thesis, returning the closed entry."""
    entry = record(**overrides)
    return journal.close(
        entry["id"], outcome=outcome, exit_price=exit_price, note=note
    )


def watch(**overrides):
    payload = {
        "ticker": "NFLX",
        "thesis": "Strong business, broken chart. Watchlist.",
        "falsifiers": ["reclaim of the 50-day at $76.80"],
        "direction": "watch",
    }
    payload.update(overrides)
    return journal.record(**payload)


class TestDerivedNumbers:
    def test_planned_r_comes_from_the_levels_not_the_stored_field(self):
        """`reward_risk` was supplied by the author and never checked."""
        entry = record(reward_risk=99.0)

        assert postmortem.describe(entry)["planned_r"] == pytest.approx(3.0)

    def test_planned_r_is_none_without_a_target(self):
        assert postmortem.describe(record(target=None))["planned_r"] is None

    def test_planned_r_is_none_when_entry_equals_stop(self):
        """Zero risk per share would divide by zero, not produce infinite reward."""
        assert postmortem.describe(record(stop=100.0))["planned_r"] is None

    def test_r_capture_compares_result_against_plan(self):
        # Planned 3R, exited at 115 for 1.5R -> half the plan captured.
        entry = closed(outcome="closed_manual", exit_price=115.0)

        assert postmortem.describe(entry)["r_capture"] == pytest.approx(0.5)

    def test_a_stop_out_captures_negative_a_third_of_a_three_r_plan(self):
        entry = closed(outcome="stopped_out", exit_price=90.0)

        assert postmortem.describe(entry)["r_capture"] == pytest.approx(-0.33)

    def test_days_held_is_computed_from_the_timestamps(self):
        assert postmortem.describe(closed())["days_held"] == 0

    def test_exit_discipline_separates_the_plan_from_the_override(self):
        assert postmortem.describe(closed())["exit_discipline"] == "plan_ran"
        assert (
            postmortem.describe(closed(outcome="stopped_out", exit_price=90.0))[
                "exit_discipline"
            ]
            == "plan_ran"
        )
        assert (
            postmortem.describe(closed(outcome="closed_manual", exit_price=110.0))[
                "exit_discipline"
            ]
            == "discretionary"
        )
        assert (
            postmortem.describe(closed(outcome="expired", exit_price=None))[
                "exit_discipline"
            ]
            == "thesis_lapsed"
        )


class TestWhatCannotBeScored:
    def test_a_watch_call_is_excluded_and_says_why(self):
        journal.close(watch()["id"], outcome="expired")

        book = postmortem.score_book()

        assert book["overall"]["count"] == 0
        assert book["watch_calls"]["closed"] == 1
        assert "watch call" in book["unscored"][0]["reason"]

    def test_a_close_without_an_exit_price_is_unscored_not_a_scratch(self):
        """Counting a missing fill as 0R would report a loss-free trade."""
        closed(outcome="expired", exit_price=None)

        book = postmortem.score_book()

        assert book["overall"]["count"] == 0
        assert book["overall"]["scratches"] == 0
        assert "exit price" in book["unscored"][0]["reason"]

    def test_unscored_theses_are_flagged_as_a_limitation(self):
        closed(outcome="expired", exit_price=None)

        assert any("excluded" in note for note in postmortem.score_book()["limitations"])

    def test_open_theses_are_not_scored_at_all(self):
        record()

        assert postmortem.score_book()["closed_theses"] == 0

    def test_the_right_reason_caveat_is_always_present(self):
        """It is never computable, so it is never conditional."""
        assert any(
            "reason given" in note for note in postmortem.score_book()["limitations"]
        )


class TestPerformance:
    def test_expectancy_is_the_mean_realised_r(self):
        closed(outcome="target_hit", exit_price=130.0)  # +3R
        closed(outcome="stopped_out", exit_price=90.0)  # -1R

        assert postmortem.score_book()["overall"]["expectancy_r"] == pytest.approx(1.0)

    def test_win_rate_and_payoff_are_reported_together(self):
        closed(outcome="target_hit", exit_price=130.0)  # +3R
        closed(outcome="stopped_out", exit_price=90.0)  # -1R
        closed(outcome="stopped_out", exit_price=90.0)  # -1R

        overall = postmortem.score_book()["overall"]

        assert overall["win_rate"] == pytest.approx(1 / 3, abs=0.001)
        assert overall["payoff_ratio"] == pytest.approx(3.0)
        assert overall["total_r"] == pytest.approx(1.0)

    def test_a_scratch_is_not_a_win(self):
        closed(outcome="closed_manual", exit_price=100.0)  # 0R

        overall = postmortem.score_book()["overall"]

        assert overall["scratches"] == 1
        assert overall["wins"] == 0
        assert overall["win_rate"] == 0.0

    def test_an_empty_book_reports_nothing_rather_than_zero(self):
        """Zero expectancy on no trades reads as a flat strategy, not no data."""
        overall = postmortem.score_book()["overall"]

        assert overall["count"] == 0
        assert overall["expectancy_r"] is None
        assert overall["win_rate"] is None

    def test_shorts_are_scored_in_the_same_units(self):
        short = journal.record(
            "TSLA",
            thesis="Fading the spike.",
            falsifiers=["close above $110"],
            direction="short",
            entry=100.0,
            stop=110.0,
            target=70.0,
        )
        journal.close(short["id"], outcome="target_hit", exit_price=80.0)

        assert postmortem.score_book()["overall"]["expectancy_r"] == pytest.approx(2.0)


class TestCalibration:
    def test_conviction_ordering_holds_when_confidence_paid(self):
        closed(conviction="low", outcome="stopped_out", exit_price=90.0)  # -1R
        closed(conviction="high", outcome="target_hit", exit_price=130.0)  # +3R

        book = postmortem.score_book()

        assert book["calibration"]["conviction_ordering_holds"] is True

    def test_ordering_breaks_when_high_conviction_underperformed(self):
        """The finding the desk least wants and most needs."""
        closed(conviction="high", outcome="stopped_out", exit_price=90.0)  # -1R
        closed(conviction="low", outcome="target_hit", exit_price=130.0)  # +3R

        book = postmortem.score_book()

        assert book["calibration"]["conviction_ordering_holds"] is False

    def test_one_bucket_has_no_ordering_to_report(self):
        closed(conviction="high", outcome="target_hit", exit_price=130.0)

        assert postmortem.score_book()["calibration"]["conviction_ordering_holds"] is None

    def test_buckets_carry_their_own_sample_size(self):
        closed(conviction="high", outcome="target_hit", exit_price=130.0)
        closed(conviction="high", outcome="stopped_out", exit_price=90.0)

        assert postmortem.score_book()["by_conviction"]["high"]["count"] == 2

    def test_average_capture_below_one_means_targets_outrun_exits(self):
        closed(outcome="closed_manual", exit_price=115.0)  # 1.5R of a 3R plan

        assert postmortem.score_book()["calibration"]["avg_r_capture"] == pytest.approx(
            0.5
        )

    def test_a_small_sample_is_named_as_a_limitation(self):
        closed()

        book = postmortem.score_book()

        assert book["calibration"]["scored_sample"] == 1
        assert any(
            f"below the {postmortem.MIN_SAMPLE}" in note for note in book["limitations"]
        )


class TestFilters:
    def test_filters_by_ticker(self):
        closed(ticker="NVDA", outcome="target_hit", exit_price=130.0)
        closed(ticker="AMD", outcome="stopped_out", exit_price=90.0)

        assert postmortem.score_book(ticker="amd")["overall"]["expectancy_r"] == (
            pytest.approx(-1.0)
        )

    def test_filters_by_horizon(self):
        closed(horizon="swing", outcome="target_hit", exit_price=130.0)
        closed(horizon="long_term", outcome="stopped_out", exit_price=90.0)

        assert postmortem.score_book(horizon="swing")["overall"]["count"] == 1

    def test_filters_by_creation_date(self):
        closed()

        assert postmortem.score_book(since="2099-01-01")["closed_theses"] == 0

    def test_an_unknown_horizon_is_refused(self):
        with pytest.raises(PostmortemError, match="horizon must be"):
            postmortem.score_book(horizon="forever")

    def test_a_malformed_since_date_is_refused(self):
        with pytest.raises(PostmortemError, match="ISO date"):
            postmortem.score_book(since="last tuesday")


class TestReview:
    def test_returns_the_plan_and_the_result_side_by_side(self):
        entry = closed(outcome="closed_manual", exit_price=115.0, note="Took it early.")

        found = postmortem.review(entry["id"])

        assert found["derived"]["planned_r"] == pytest.approx(3.0)
        assert found["derived"]["realised_r"] == pytest.approx(1.5)
        assert found["derived"]["r_capture"] == pytest.approx(0.5)
        assert found["close_note"] == "Took it early."

    def test_falsifiers_come_back_unchecked(self):
        """Answering them from the record would be inventing the answer."""
        entry = closed()

        assert postmortem.review(entry["id"])["falsifiers_to_check"] == [
            "close below $90"
        ]

    def test_evidence_and_gaps_survive_verbatim(self):
        entry = record(
            evidence=[{"claim": "revenue $416.2B", "source": "10-K", "period": "FY2025"}],
            gaps=["Q4 quarterly revenue not derivable"],
        )
        journal.close(entry["id"], outcome="target_hit", exit_price=130.0)

        found = postmortem.review(entry["id"])

        assert found["evidence_as_cited"][0]["period"] == "FY2025"
        assert found["gaps_at_the_time"] == ["Q4 quarterly revenue not derivable"]

    def test_known_gaps_reframe_the_judgement(self):
        entry = record(gaps=["no Q4 figure"])
        journal.close(entry["id"], outcome="target_hit", exit_price=130.0)

        assert any(
            "knowable then" in note for note in postmortem.review(entry["id"])["limitations"]
        )

    def test_an_open_thesis_is_reviewable_for_its_falsifiers(self):
        entry = record()

        found = postmortem.review(entry["id"])

        assert found["derived"]["realised_r"] is None
        assert any("still open" in note for note in found["limitations"])

    def test_a_missing_close_note_is_a_named_gap(self):
        entry = closed(note="")

        assert any(
            "No close note" in note for note in postmortem.review(entry["id"])["limitations"]
        )

    def test_a_manual_close_says_neither_level_was_tested(self):
        entry = closed(outcome="closed_manual", exit_price=115.0, note="Nerves.")

        assert any(
            "Closed manually" in note
            for note in postmortem.review(entry["id"])["limitations"]
        )

    def test_a_missing_target_blocks_judging_against_the_plan(self):
        entry = record(target=None)
        journal.close(entry["id"], outcome="closed_manual", exit_price=115.0)

        assert any(
            "against its own plan" in note
            for note in postmortem.review(entry["id"])["limitations"]
        )

    def test_an_unknown_thesis_is_refused(self):
        with pytest.raises(JournalError, match="no thesis"):
            postmortem.review("2026-01-01-AAPL-abcdef")
