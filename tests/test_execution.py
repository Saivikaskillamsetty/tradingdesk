"""The execution gate, tested against a fake broker.

These run offline and never touch a broker. What is being tested is mostly
refusal: an order that should not have been sent looks identical to one that
should have, right up until it fills. Every path that reaches `_request` in a
test would be a real order in production, so the fake records calls instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from desk_mcp import execution, journal
from desk_mcp.execution import ExecutionError

PAPER_ACCOUNT = {
    "account_number": "PA3ABCDEF",
    "status": "ACTIVE",
    "equity": "100000",
    "cash": "100000",
    "buying_power": "200000",
    "long_market_value": "0",
}


class FakeBroker:
    """Records requests instead of sending them."""

    def __init__(self, account: dict | None = None, **responses: Any):
        self.account = account if account is not None else PAPER_ACCOUNT
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if path == "/account":
            return self.account
        if path == "/orders" and method == "POST":
            payload = kwargs["json"]
            return {
                "id": "broker-order-1",
                "client_order_id": payload["client_order_id"],
                "symbol": payload["symbol"],
                "qty": payload["qty"],
                "side": payload["side"],
                "order_class": payload["order_class"],
                "limit_price": payload["limit_price"],
                "status": "accepted",
                "submitted_at": "2026-08-05T14:00:00Z",
            }
        return self.responses.get(path, [])

    @property
    def orders_sent(self) -> list[dict]:
        return [k["json"] for m, p, k in self.calls if p == "/orders" and m == "POST"]


@pytest.fixture(autouse=True)
def scratch_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")


@pytest.fixture
def broker(monkeypatch):
    fake = FakeBroker()
    monkeypatch.setattr(execution, "_request", fake)
    return fake


def approved_thesis(**overrides) -> dict:
    payload = {
        "ticker": "NVDA",
        "thesis": "Trend intact, buying the 50-day.",
        "falsifiers": ["close below $150"],
        "direction": "long",
        "entry": 200.0,
        "stop": 180.0,
        "target": 250.0,
        "shares": 50,
        "dollar_risk": 1_000.0,
        "risk_verdict": "approved",
    }
    payload.update(overrides)
    return journal.record(**payload)


class TestPaperGuard:
    def test_live_account_number_is_refused(self, monkeypatch):
        """Live keys against the paper URL must fail closed."""
        fake = FakeBroker(account={**PAPER_ACCOUNT, "account_number": "123456789"})
        monkeypatch.setattr(execution, "_request", fake)

        with pytest.raises(ExecutionError, match="paper"):
            execution.account()

    def test_paper_account_is_accepted(self, broker):
        assert execution.account()["paper"] is True

    def test_no_order_is_sent_when_the_account_is_not_paper(self, monkeypatch):
        fake = FakeBroker(account={**PAPER_ACCOUNT, "account_number": "999"})
        monkeypatch.setattr(execution, "_request", fake)
        thesis = approved_thesis()

        with pytest.raises(ExecutionError):
            execution.place_order(thesis["id"])

        assert fake.orders_sent == []

    def test_the_base_url_is_the_paper_endpoint(self):
        """Hardcoded on purpose — no env var may redirect this."""
        assert "paper-api.alpaca.markets" in execution.PAPER_BASE


class TestAuthorisation:
    """Every reason a thesis may not reach the broker."""

    def test_an_approved_thesis_is_sent(self, broker):
        result = execution.place_order(approved_thesis()["id"])

        assert result["order"]["broker_order_id"] == "broker-order-1"
        assert len(broker.orders_sent) == 1

    def test_approved_with_warnings_is_sent(self, broker):
        thesis = approved_thesis(risk_verdict="approved_with_warnings")

        assert execution.place_order(thesis["id"])["order"]["status"] == "accepted"

    def test_a_vetoed_thesis_is_refused(self, broker):
        thesis = approved_thesis(risk_verdict="vetoed")

        with pytest.raises(ExecutionError, match="vetoed"):
            execution.place_order(thesis["id"])

        assert broker.orders_sent == []

    def test_a_thesis_with_no_risk_pass_is_refused(self, broker):
        """Unsized is not the same as approved."""
        thesis = approved_thesis(risk_verdict=None)

        with pytest.raises(ExecutionError, match="risk verdict"):
            execution.place_order(thesis["id"])

    def test_a_watch_call_is_refused(self, broker):
        thesis = journal.record(
            "NFLX",
            thesis="Watchlist only.",
            falsifiers=["reclaim of $76.80"],
            direction="watch",
        )

        with pytest.raises(ExecutionError, match="takes no"):
            execution.place_order(thesis["id"])

    def test_a_closed_thesis_is_refused(self, broker):
        thesis = approved_thesis()
        journal.close(thesis["id"], outcome="expired")

        with pytest.raises(ExecutionError, match="only an open"):
            execution.place_order(thesis["id"])

    def test_a_zero_share_thesis_is_refused(self, broker):
        thesis = approved_thesis(shares=0)

        with pytest.raises(ExecutionError, match="no position to open"):
            execution.place_order(thesis["id"])

    def test_a_missing_thesis_is_refused(self, broker):
        with pytest.raises(journal.JournalError, match="no thesis"):
            execution.place_order("2026-01-01-AAPL-abcdef")


class TestIdempotency:
    def test_a_second_order_against_the_same_thesis_is_refused(self, broker):
        """A retried call must not open a second position in the name."""
        thesis_id = approved_thesis()["id"]
        execution.place_order(thesis_id)

        with pytest.raises(ExecutionError, match="already has order"):
            execution.place_order(thesis_id)

        assert len(broker.orders_sent) == 1

    def test_the_order_is_recorded_on_the_thesis(self, broker):
        thesis_id = approved_thesis()["id"]
        execution.place_order(thesis_id)

        stored = journal.load(thesis_id)["execution"]

        assert stored["broker_order_id"] == "broker-order-1"
        assert stored["paper"] is True
        assert stored["attached_at"]

    def test_the_client_order_id_carries_the_thesis(self, broker):
        """So a broker-side order can be traced back to the call behind it."""
        thesis_id = approved_thesis()["id"]
        execution.place_order(thesis_id)

        assert broker.orders_sent[0]["client_order_id"] == f"desk-{thesis_id}"


class TestOrderPayload:
    def test_quantity_and_price_come_from_the_thesis(self, broker):
        execution.place_order(approved_thesis()["id"])
        sent = broker.orders_sent[0]

        assert sent["qty"] == "50"
        assert sent["limit_price"] == "200.0"
        assert sent["symbol"] == "NVDA"

    def test_a_long_buys(self, broker):
        execution.place_order(approved_thesis()["id"])

        assert broker.orders_sent[0]["side"] == "buy"

    def test_a_short_sells(self, broker):
        thesis = approved_thesis(
            direction="short", entry=200.0, stop=220.0, target=160.0
        )
        execution.place_order(thesis["id"])

        assert broker.orders_sent[0]["side"] == "sell"

    def test_the_stop_goes_out_with_the_entry(self, broker):
        """A stop added later is a stop that might never be added."""
        execution.place_order(approved_thesis()["id"])
        sent = broker.orders_sent[0]

        assert sent["order_class"] == "bracket"
        assert sent["stop_loss"]["stop_price"] == "180.0"
        assert sent["take_profit"]["limit_price"] == "250.0"

    def test_without_a_target_it_is_an_oto_that_still_carries_the_stop(self, broker):
        thesis = approved_thesis(target=None)
        execution.place_order(thesis["id"])
        sent = broker.orders_sent[0]

        assert sent["order_class"] == "oto"
        assert sent["stop_loss"]["stop_price"] == "180.0"
        assert "take_profit" not in sent


class TestReconciliation:
    def positions(self, monkeypatch, *symbols: str) -> FakeBroker:
        fake = FakeBroker()
        fake.responses["/positions"] = [
            {
                "symbol": s,
                "qty": "50",
                "side": "long",
                "avg_entry_price": "200",
                "market_value": "10000",
                "unrealized_pl": "0",
                "unrealized_plpc": "0",
            }
            for s in symbols
        ]
        monkeypatch.setattr(execution, "_request", fake)
        return fake

    def test_a_position_with_a_thesis_matches(self, monkeypatch):
        self.positions(monkeypatch, "NVDA")
        approved_thesis()

        result = execution.reconcile()

        assert result["matched"] == ["NVDA"]
        assert result["in_sync"] is True

    def test_a_position_with_no_thesis_is_flagged_as_untracked(self, monkeypatch):
        """Portfolio heat cannot see this, which is the point of the check."""
        self.positions(monkeypatch, "TSLA")

        result = execution.reconcile()

        assert [p["symbol"] for p in result["untracked_positions"]] == ["TSLA"]
        assert result["in_sync"] is False

    def test_a_thesis_with_no_position_is_flagged_separately(self, monkeypatch):
        self.positions(monkeypatch)
        approved_thesis()

        result = execution.reconcile()

        assert [t["ticker"] for t in result["theses_without_position"]] == ["NVDA"]
        assert result["untracked_positions"] == []

    def test_watch_calls_are_not_expected_to_have_positions(self, monkeypatch):
        self.positions(monkeypatch)
        journal.record(
            "NFLX",
            thesis="Watchlist only.",
            falsifiers=["reclaim of $76.80"],
            direction="watch",
        )

        assert execution.reconcile()["in_sync"] is True
