"""Shared rendering for the desk UI.

The rules that govern the agents govern this app, for the same reasons. A
figure appears with the period it covers or it does not appear. A `limitations`
list is rendered where the reader will see it, never behind a collapsed panel,
because a limitation nobody reads is a limitation that does not exist. An error
is shown as a gap rather than an empty table, because an empty table looks like
an answer.

Nothing here calls a language model. Every number rendered by this app was
computed in `desk_mcp`, which is the same code the MCP server exposes to the
agents -- one implementation, two front ends.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import pandas as pd
import streamlit as st

def read_only() -> bool:
    """Whether writes are disabled.

    Read from the environment on every call rather than captured at import,
    for the same reason `journal.theses_dir()` is: a value bound at import
    time depends on when the module was first loaded, and a security switch
    whose state depends on import order is not a security switch.
    """
    return os.environ.get("DESK_UI_READONLY", "").strip().lower() in ("1", "true", "yes")

# Long enough that clicking between pages does not refetch, short enough that a
# session does not run on yesterday's tape. Filed documents are cached to disk
# permanently by `desk_mcp.cache` underneath this anyway.
TTL_SLOW = 3600
TTL_FAST = 300


def page(title: str, icon: str, blurb: str) -> None:
    """Standard page header."""
    st.set_page_config(page_title=f"{title} — desk", page_icon=icon, layout="wide")
    st.title(title)
    st.caption(blurb)


def is_error(payload: Any) -> bool:
    return isinstance(payload, dict) and "error" in payload


def show_error(payload: dict[str, Any]) -> None:
    """Render a tool failure as a named gap.

    The desk's rule is that a missing figure is reported, never substituted.
    The UI equivalent is that a failed call says what is missing rather than
    rendering an empty component that reads as "nothing here".
    """
    st.error(f"**{payload.get('error', 'Error')}** — {payload.get('message', '')}")
    if payload.get("guidance"):
        st.caption(payload["guidance"])


def guard(fn: Callable[[], Any]) -> Any | None:
    """Run a data call, rendering any failure and returning None."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader, not swallowed
        st.error(f"**{type(exc).__name__}** — {exc}")
        return None
    if is_error(result):
        show_error(result)
        return None
    return result


def limitations(items: list[str] | None, title: str = "What this does not tell you") -> None:
    """Render limitations prominently.

    Deliberately not an expander. These are the checks that did not run, and
    hiding them one click away is how a provisional answer starts reading as a
    clean one.
    """
    if not items:
        return
    st.markdown(f"**{title}**")
    # Rendered as native markdown rather than raw HTML so that backticks and
    # emphasis in the source text come out formatted instead of literal.
    with st.container(border=True):
        for item in items:
            st.markdown(f"- {item}")


def metric_row(pairs: list[tuple[str, Any, str | None]]) -> None:
    """A row of metrics. Each is (label, value, help)."""
    columns = st.columns(len(pairs))
    for column, (label, value, helptext) in zip(columns, pairs):
        column.metric(label, "—" if value is None else value, help=helptext)


def money(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= cutoff:
            return f"${value / cutoff:,.2f}{suffix}"
    return f"${value:,.{digits}f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}%"


def ratio(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def frame(rows: list[dict[str, Any]], columns: dict[str, str] | None = None) -> pd.DataFrame:
    """Build a display frame, optionally renaming and ordering columns."""
    if not rows:
        return pd.DataFrame()
    data = pd.DataFrame(rows)
    if columns:
        keep = [c for c in columns if c in data.columns]
        data = data[keep].rename(columns=columns)
    return data


def table(rows: list[dict[str, Any]], columns: dict[str, str] | None = None, empty: str = "Nothing to show.") -> None:
    data = frame(rows, columns)
    if data.empty:
        st.caption(empty)
        return
    try:
        st.dataframe(data, width="stretch", hide_index=True)
    except Exception:  # noqa: BLE001
        # A column mixing types cannot be serialised for display. Showing it
        # as text beats failing the whole page over a formatting problem.
        st.dataframe(data.astype(str), width="stretch", hide_index=True)


def provenance(*parts: str | None) -> None:
    """A caption naming where the figures above came from."""
    shown = [p for p in parts if p]
    if shown:
        st.caption(" · ".join(shown))


def read_only_notice() -> bool:
    """Warn when writes are disabled. Returns True when writes are blocked."""
    blocked = read_only()
    if blocked:
        st.info(
            "Read-only mode (`DESK_UI_READONLY`). Recording and closing calls "
            "is disabled."
        )
    return blocked


def sidebar_note() -> None:
    st.sidebar.markdown("### Trading desk")
    st.sidebar.caption(
        "Every figure here is computed in `desk_mcp` — the same code the "
        "research agents call. No language model runs in this app, and "
        "nothing here places an order."
    )
    if read_only():
        st.sidebar.warning("Read-only")
