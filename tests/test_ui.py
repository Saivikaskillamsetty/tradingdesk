"""The dashboard, exercised through Streamlit's own test harness.

A Streamlit page that imports cleanly can still fail on first render, so these
run the scripts rather than the modules. What matters most is that the two
journal guards actually block: the UI is the one place on this desk where a
call can be written without an agent reading the rules first, so the rules have
to live in the form.

No network is required for the pages themselves -- an unreachable data source
renders as a named gap, which these tests assert is what happens.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("streamlit", reason="UI extra not installed: uv sync --extra ui")

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from desk_mcp import cache, journal  # noqa: E402

UI_ROOT = pathlib.Path(__file__).resolve().parents[1] / "desk_ui"
PAGES = [UI_ROOT / "Home.py", *sorted((UI_ROOT / "pages").glob("*.py"))]


@pytest.fixture(autouse=True)
def scratch_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_THESES_DIR", str(tmp_path))
    monkeypatch.delenv("DESK_UI_READONLY", raising=False)

    # `st.cache_data` lives in the process, not in the AppTest, so a result
    # cached by one test is served to the next one -- including results
    # computed while a credential was still present.
    st.cache_data.clear()

    # The on-disk cache underneath is shared with the developer's real session,
    # where it may already hold a live FRED or EDGAR response. A test asserting
    # what happens without a credential would then be served the cached answer
    # and pass for the wrong reason. Patched as an attribute because
    # `cache.CACHE_DIR` is bound at import and cannot be redirected by setenv.
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")

    return tmp_path


def run(page: pathlib.Path) -> AppTest:
    return AppTest.from_file(str(page), default_timeout=120).run()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_every_page_renders(page):
    """A page that raises shows the user a stack trace instead of the desk."""
    app = run(page)

    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_every_page_has_a_title(page):
    assert len(run(page).title) == 1


class TestEmptyBook:
    def test_home_reports_an_empty_book_as_a_state_not_a_failure(self):
        app = run(UI_ROOT / "Home.py")
        text = " ".join(c.value for c in app.caption)

        assert "valid state" in text

    def test_the_scoreboard_says_there_is_nothing_to_score(self):
        app = run(UI_ROOT / "pages" / "2_Scoreboard.py")

        assert any("nothing to score" in i.value for i in app.info)


class TestJournalGuards:
    """The rules that make a call scoreable, enforced in the form."""

    def page(self) -> AppTest:
        return run(UI_ROOT / "pages" / "6_Journal.py")

    def test_a_thesis_without_a_falsifier_is_refused(self, scratch_journal):
        app = self.page()
        app.text_input[0].set_value("NVDA")
        app.text_area[0].set_value("A call nothing could disprove.")
        app.button[0].click().run()

        assert any("falsifier is required" in e.value for e in app.error)
        assert list(scratch_journal.glob("*.json")) == []

    def test_a_complete_thesis_records(self, scratch_journal):
        app = self.page()
        app.text_input[0].set_value("NVDA")
        app.selectbox[0].set_value("long")
        app.text_area[0].set_value("Trend intact, buying the 50-day.")
        app.number_input[0].set_value(200.0)
        app.number_input[1].set_value(190.0)
        app.text_area[1].set_value("daily close below $190")
        app.button[0].click().run()

        assert any("Recorded" in s.value for s in app.success)
        assert len(list(scratch_journal.glob("*.json"))) == 1

    def test_closing_a_position_without_an_exit_price_is_blocked(self):
        """Without a fill there is no realised R, permanently."""
        entry = journal.record(
            "AMD",
            thesis="Buying the breakout.",
            falsifiers=["close below $45"],
            direction="long",
            entry=50.0,
            stop=45.0,
        )

        app = self.page()
        app.button[1].click().run()

        assert any("No exit price" in e.value for e in app.error)
        assert journal.load(entry["id"])["status"] == "open"

    def test_a_watch_call_closes_without_a_price(self):
        """Watch calls have no fill by design, so the guard must not fire."""
        entry = journal.record(
            "NFLX",
            thesis="Watching for a reclaim.",
            falsifiers=["reclaim of $80"],
            direction="watch",
        )

        app = self.page()
        app.button[1].click().run()

        assert journal.load(entry["id"])["status"] == "closed"


class TestReadOnlyMode:
    def test_writes_are_disabled_when_the_flag_is_set(self, monkeypatch):
        """The single switch that makes this app safe to expose."""
        monkeypatch.setenv("DESK_UI_READONLY", "1")

        app = run(UI_ROOT / "pages" / "6_Journal.py")

        assert any("Read-only" in i.value for i in app.info)
        assert all(button.disabled for button in app.button)


class TestRenderedShapes:
    """Pages must read the shape the modules actually return.

    A page that reads a key nobody emits raises nothing: it renders an empty
    table under a confident heading, which is indistinguishable from a real
    answer of "nothing here". So these assert on content, not on the absence
    of an exception.
    """

    def test_sizing_renders_the_real_verdict_and_checks(self):
        app = run(UI_ROOT / "pages" / "3_Sizing.py")

        app.text_input[0].set_value("NVDA")
        app.number_input[0].set_value(100_000.0)  # equity
        app.number_input[1].set_value(200.0)  # entry
        app.number_input[2].set_value(190.0)  # stop
        app.number_input[3].set_value(230.0)  # target
        app.number_input[4].set_value(8.0)  # atr
        app.button[0].click().run()

        assert not app.exception, [e.value for e in app.exception]

        # The verdict banner must say something, and every rule checked must
        # reach the table rather than silently vanishing on a renamed key.
        assert app.success or app.warning or app.error
        assert app.dataframe, "the checks table did not render"
        checks = app.dataframe[0].value
        assert len(checks) > 0
        assert "Rule" in checks.columns

    def test_sizing_surfaces_shares_rather_than_an_empty_metric(self):
        app = run(UI_ROOT / "pages" / "3_Sizing.py")

        app.text_input[0].set_value("NVDA")
        app.number_input[0].set_value(100_000.0)
        app.number_input[1].set_value(200.0)
        app.number_input[2].set_value(190.0)
        app.number_input[3].set_value(230.0)
        app.button[0].click().run()

        shown = {m.label: m.value for m in app.metric}

        assert shown.get("Shares") not in (None, "—"), shown

    def test_sizing_requires_equity_before_it_computes_anything(self):
        """A share count sized against a placeholder reads exactly like a real one."""
        app = run(UI_ROOT / "pages" / "3_Sizing.py")

        app.text_input[0].set_value("NVDA")
        app.number_input[1].set_value(200.0)
        app.number_input[2].set_value(190.0)
        app.button[0].click().run()

        assert any("required" in e.value for e in app.error)
        assert not app.metric


class TestMissingDataIsAGap:
    def test_a_missing_credential_renders_as_a_named_error(self, monkeypatch):
        """An empty table would read as 'nothing here' rather than 'unavailable'."""
        for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FRED_API_KEY"):
            monkeypatch.delenv(name, raising=False)

        app = run(UI_ROOT / "pages" / "5_Market.py")

        assert app.error, "a missing key must be surfaced, not swallowed"
        assert any(
            "FRED_API_KEY" in e.value or "ALPACA" in e.value for e in app.error
        ), [e.value for e in app.error]
