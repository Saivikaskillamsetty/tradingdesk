"""Sizing arithmetic and the veto rules, tested on synthetic inputs.

These run offline. Sizing is the one place on the desk where being wrong is
both easy and invisible: a share count off by a factor of ten reads exactly
like a correct one, and the error only surfaces once the position exists.
"""

from __future__ import annotations

import pytest

from desk_mcp import journal, risk
from desk_mcp.risk import RiskError

EQUITY = 100_000.0


@pytest.fixture(autouse=True)
def empty_journal(tmp_path, monkeypatch):
    """No open theses unless a test creates them, so heat starts at zero."""
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    return tmp_path


def size(**overrides):
    payload = {
        "ticker": "nvda",
        "direction": "long",
        "entry": 100.0,
        "stop": 90.0,
        "target": 130.0,
        "account_equity": EQUITY,
        "atr": 5.0,
    }
    payload.update(overrides)
    return risk.size_position(**payload)


def check(result, rule):
    return next(c for c in result["checks"] if c["rule"] == rule)


class TestSizing:
    def test_shares_come_from_the_risk_budget(self):
        # 1% of 100k is 1,000 of risk; 10 per share buys 100 shares.
        assert size()["sizing"]["shares"] == 100

    def test_dollar_risk_matches_the_budget(self):
        assert size()["sizing"]["dollar_risk"] == pytest.approx(1_000.0)

    def test_share_count_rounds_down(self):
        """Rounding up would breach the limit it was meant to enforce."""
        # 1,000 of budget at 6 per share is 166.67 shares.
        result = size(entry=100.0, stop=94.0, atr=3.0)

        assert result["sizing"]["shares"] == 166
        assert result["sizing"]["capped_by"] is None

    def test_percent_of_equity_at_risk_is_reported(self):
        assert size()["sizing"]["pct_equity_at_risk"] == pytest.approx(1.0)

    def test_a_wider_stop_buys_fewer_shares(self):
        tight = size(stop=95.0, atr=2.0)["sizing"]["shares"]
        wide = size(stop=80.0, atr=8.0)["sizing"]["shares"]

        assert wide < tight

    def test_short_direction_sizes_the_same_way(self):
        result = size(direction="short", entry=100.0, stop=110.0, target=70.0)

        assert result["sizing"]["shares"] == 100
        assert result["verdict"] == "approved"

    def test_requested_risk_above_policy_is_clamped_and_flagged(self):
        result = size(risk_pct=5.0)

        assert result["sizing"]["risk_pct_applied"] == 1.0
        assert result["sizing"]["shares"] == 100
        assert not check(result, "max_risk_pct_per_trade")["passed"]
        assert result["verdict"] == "approved_with_warnings"

    def test_requested_risk_below_policy_is_honoured(self):
        result = size(risk_pct=0.5)

        assert result["sizing"]["shares"] == 50
        assert result["verdict"] == "approved"


class TestConcentration:
    def test_a_tight_stop_is_capped_by_position_size(self):
        """A tight stop makes a huge position look cheap; gaps disagree."""
        # 1,000 of budget at 0.10 per share would be 10,000 shares, or 100%
        # of the account. The 20% cap allows 2,000.
        result = size(entry=10.0, stop=9.9, target=12.0, atr=0.05)

        assert result["sizing"]["shares"] == 2_000
        assert result["sizing"]["capped_by"] == "max_position_pct"
        assert result["sizing"]["position_pct_of_equity"] == pytest.approx(20.0)

    def test_capping_reduces_the_dollar_risk_too(self):
        result = size(entry=10.0, stop=9.9, target=12.0, atr=0.05)

        # 2,000 shares at 0.10 of risk is 200, not the full 1,000 budget.
        assert result["sizing"]["dollar_risk"] == pytest.approx(200.0)

    def test_an_uncapped_position_reports_no_cap(self):
        assert size()["sizing"]["capped_by"] is None


class TestRewardRisk:
    def test_below_the_minimum_is_vetoed(self):
        # 100 -> 115 is 15 of reward on 10 of risk.
        result = size(target=115.0)

        assert result["verdict"] == "vetoed"
        assert check(result, "min_reward_risk")["observed"] == pytest.approx(1.5)

    def test_exactly_the_minimum_passes(self):
        result = size(target=120.0)

        assert check(result, "min_reward_risk")["passed"]
        assert result["verdict"] == "approved"

    def test_a_target_on_the_wrong_side_is_vetoed(self):
        result = size(target=95.0)

        assert result["verdict"] == "vetoed"
        assert "losing side" in check(result, "min_reward_risk")["message"]

    def test_a_missing_target_warns_and_is_recorded_as_unchecked(self):
        result = size(target=None)

        assert result["verdict"] == "approved_with_warnings"
        assert any("reward:risk" in note for note in result["limitations"])


class TestStopVersusNoise:
    def test_a_stop_inside_daily_noise_is_vetoed(self):
        # 10 of stop distance against an ATR of 8 is 1.25x.
        result = size(atr=8.0)

        assert result["verdict"] == "vetoed"
        assert check(result, "min_stop_atr_multiple")["observed"] == pytest.approx(1.25)

    def test_a_stop_beyond_the_minimum_passes(self):
        assert check(size(atr=5.0), "min_stop_atr_multiple")["passed"]

    def test_a_missing_atr_warns_rather_than_assuming_the_check_passed(self):
        result = size(atr=None)

        assert result["verdict"] == "approved_with_warnings"
        assert result["levels"]["stop_atr_multiple"] is None
        assert any("ATR" in note for note in result["limitations"])


class TestPortfolioHeat:
    def open_position(self, ticker: str, dollar_risk: float):
        journal.record(
            ticker,
            thesis="Recorded for heat.",
            falsifiers=["stop taken out"],
            direction="long",
            entry=100.0,
            stop=90.0,
            dollar_risk=dollar_risk,
        )

    def test_heat_starts_at_zero_with_an_empty_journal(self):
        result = size()

        assert result["portfolio"]["open_heat_pct"] == 0
        assert result["portfolio"]["heat_after_this_trade_pct"] == pytest.approx(1.0)

    def test_open_theses_add_to_heat(self):
        self.open_position("AMD", 2_000.0)
        result = size()

        assert result["portfolio"]["open_heat_pct"] == pytest.approx(2.0)
        assert result["portfolio"]["heat_after_this_trade_pct"] == pytest.approx(3.0)
        assert result["verdict"] == "approved"

    def test_breaching_the_ceiling_is_vetoed(self):
        self.open_position("AMD", 5_500.0)
        result = size()

        assert result["verdict"] == "vetoed"
        assert check(result, "max_portfolio_heat_pct")["observed"] == pytest.approx(6.5)

    def test_closed_theses_stop_counting(self):
        self.open_position("AMD", 5_500.0)
        journal.close(journal.search(ticker="AMD")[0]["id"], outcome="stopped_out")

        assert size()["verdict"] == "approved"

    def test_unjournalled_exposure_is_declared_as_a_blind_spot(self):
        assert any("journalled" in note for note in size()["limitations"])


class TestMalformedRequests:
    """Incoherent input raises; it is not a trade the desk declines."""

    def test_long_stop_above_entry_raises(self):
        with pytest.raises(RiskError, match="below the entry"):
            size(stop=110.0)

    def test_short_stop_below_entry_raises(self):
        with pytest.raises(RiskError, match="above the entry"):
            size(direction="short", stop=90.0)

    def test_zero_risk_per_share_raises(self):
        with pytest.raises(RiskError, match="risk per share is zero"):
            size(stop=100.0)

    def test_negative_equity_raises(self):
        with pytest.raises(RiskError, match="equity must be positive"):
            size(account_equity=-1.0)

    def test_unknown_direction_raises(self):
        with pytest.raises(RiskError, match="direction must be"):
            size(direction="hedged")


class TestAccountTooSmall:
    def test_a_stop_wider_than_the_budget_is_vetoed_not_rounded_up(self):
        """One share would breach the limit, so there is no position here."""
        result = risk.size_position(
            "BRK.A",
            direction="long",
            entry=700_000.0,
            stop=650_000.0,
            target=850_000.0,
            account_equity=10_000.0,
            atr=20_000.0,
        )

        assert result["sizing"]["shares"] == 0
        assert result["verdict"] == "vetoed"


class TestPolicyIsExplainable:
    def test_every_rule_states_its_rationale(self):
        assert all(r["rationale"] for r in risk.policy()["rules"])

    def test_the_result_carries_the_policy_it_was_judged_against(self):
        assert size()["policy"]["min_reward_risk"]["value"] == 2.0

    def test_every_check_names_the_rule_it_applied(self):
        rules = set(risk.POLICY)

        assert all(c["rule"] in rules for c in size()["checks"])

    def test_a_veto_is_reported_with_a_reason(self):
        result = size(target=115.0)

        assert result["vetoes"]
        assert all(message for message in result["vetoes"])
