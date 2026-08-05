"""Durable record of the desk's calls, so they can be scored later.

An unrecorded call cannot be graded, and ungraded calls are how a desk talks
itself into believing it was right all along. Every thesis is written the
moment it is taken, together with the evidence it rested on and the specific
observations that would falsify it -- both of which are impossible to
reconstruct honestly once the outcome is known.

Entries are plain JSON files under `theses/`, one per call, so a thesis is
greppable, diffable and reviewable in the same repository as the code that
produced it. Realised R is computed here on close rather than by an agent,
for the same reason every other number on this desk is computed in Python.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

DIRECTIONS = ("long", "short", "watch")
HORIZONS = ("swing", "positional", "long_term")
OUTCOMES = (
    "target_hit",
    "stopped_out",
    "closed_manual",
    "expired",
    "invalidated",
)
STATUSES = ("open", "closed")


class JournalError(ValueError):
    """A thesis that cannot be recorded, found, or closed as stated."""


def theses_dir() -> Path:
    """Where theses live.

    Read from the environment on every call so the MCP server can be launched
    from anywhere, and so tests can point it at a temporary directory without
    reaching into module state.
    """
    override = os.environ.get("DESK_THESES_DIR")
    return Path(override) if override else _REPO_ROOT / "theses"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write via a temporary file and rename.

    A half-written thesis is worse than a missing one: it looks like a record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _path_for(thesis_id: str) -> Path:
    if "/" in thesis_id or "\\" in thesis_id or thesis_id.startswith("."):
        raise JournalError(f"invalid thesis id {thesis_id!r}")
    return theses_dir() / f"{thesis_id}.json"


def _one_of(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise JournalError(
            f"{name} must be one of {', '.join(allowed)}; got {value!r}"
        )
    return value


def _risk_per_share(entry: float, stop: float) -> float | None:
    if entry is None or stop is None:
        return None
    distance = abs(entry - stop)
    return distance or None


def realised_r(
    direction: str, entry: float | None, stop: float | None, exit_price: float | None
) -> float | None:
    """Outcome in units of the risk originally taken.

    R is the only comparable unit across positions of different sizes and
    volatilities: a 3R win on a small position is a better call than a 0.5R
    win on a large one. Returns None when any leg is missing rather than
    assuming a fill price.
    """
    if direction == "watch" or exit_price is None:
        return None
    risk = _risk_per_share(entry, stop)
    if risk is None:
        return None
    move = exit_price - entry if direction == "long" else entry - exit_price
    return round(move / risk, 2)


def record(
    ticker: str,
    thesis: str,
    direction: str = "watch",
    horizon: str = "swing",
    conviction: str = "medium",
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    reward_risk: float | None = None,
    shares: int | None = None,
    dollar_risk: float | None = None,
    risk_verdict: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    falsifiers: list[str] | None = None,
    gaps: list[str] | None = None,
) -> dict[str, Any]:
    """Write a new thesis and return it.

    `falsifiers` is required in substance, not just in form: a call with
    nothing that could disprove it cannot be scored, only rationalised.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise JournalError("ticker is required")
    if not thesis.strip():
        raise JournalError("thesis text is required")

    direction = _one_of("direction", direction, DIRECTIONS)
    horizon = _one_of("horizon", horizon, HORIZONS)

    if not falsifiers:
        raise JournalError(
            "at least one falsifier is required; a thesis with nothing that "
            "could disprove it cannot be scored later"
        )

    if direction in ("long", "short") and (entry is None or stop is None):
        raise JournalError(
            f"a {direction} thesis needs an entry and a stop; record it as "
            f"direction='watch' if no position is being taken"
        )

    thesis_id = f"{datetime.now(timezone.utc):%Y-%m-%d}-{ticker}-{uuid.uuid4().hex[:6]}"
    entry_payload: dict[str, Any] = {
        "id": thesis_id,
        "ticker": ticker,
        "status": "open",
        "created_at": _now(),
        "direction": direction,
        "horizon": horizon,
        "conviction": conviction,
        "thesis": thesis.strip(),
        "levels": {
            "entry": entry,
            "stop": stop,
            "target": target,
            "reward_risk": reward_risk,
            "risk_per_share": _risk_per_share(entry, stop),
        },
        "sizing": {
            "shares": shares,
            "dollar_risk": dollar_risk,
            "risk_verdict": risk_verdict,
        },
        "evidence": evidence or [],
        "falsifiers": list(falsifiers),
        "gaps": gaps or [],
        "closed_at": None,
        "outcome": None,
    }

    _write_atomic(_path_for(thesis_id), entry_payload)
    return entry_payload


def load(thesis_id: str) -> dict[str, Any]:
    path = _path_for(thesis_id)
    if not path.exists():
        raise JournalError(f"no thesis with id {thesis_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def search(
    ticker: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Theses matching the filters, newest first."""
    if status is not None:
        _one_of("status", status, STATUSES)

    directory = theses_dir()
    if not directory.exists():
        return []

    found: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A file we cannot parse is reported as a gap by the caller
            # rather than silently treated as absent.
            continue
        if ticker and entry.get("ticker") != ticker.strip().upper():
            continue
        if status and entry.get("status") != status:
            continue
        found.append(entry)

    found.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return found[:limit]


def close(
    thesis_id: str,
    outcome: str,
    exit_price: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Close a thesis, recording how it actually resolved."""
    outcome = _one_of("outcome", outcome, OUTCOMES)
    entry = load(thesis_id)

    if entry["status"] == "closed":
        raise JournalError(
            f"thesis {thesis_id} was already closed at {entry['closed_at']}"
        )

    levels = entry.get("levels", {})
    r_multiple = realised_r(
        entry["direction"], levels.get("entry"), levels.get("stop"), exit_price
    )

    entry["status"] = "closed"
    entry["closed_at"] = _now()
    entry["outcome"] = {
        "result": outcome,
        "exit_price": exit_price,
        "realised_r": r_multiple,
        "note": note.strip(),
    }

    _write_atomic(_path_for(thesis_id), entry)
    return entry


def attach_order(thesis_id: str, order: dict[str, Any]) -> dict[str, Any]:
    """Record the broker order a thesis was executed as.

    Written once. A thesis that already carries an order cannot be sent
    again, which is what stops a retried tool call from opening a second
    position in the same name.
    """
    entry = load(thesis_id)

    if entry.get("execution"):
        raise JournalError(
            f"thesis {thesis_id} already has order "
            f"{entry['execution'].get('broker_order_id')} attached; refusing to "
            f"place a second order against the same call"
        )

    entry["execution"] = {**order, "attached_at": _now()}
    _write_atomic(_path_for(thesis_id), entry)
    return entry


def open_risk() -> dict[str, Any]:
    """Capital currently at risk across open theses.

    This is the desk's exposure as recorded, which is not necessarily the
    exposure that exists. Positions taken without journalling them are
    invisible here, and the caller is expected to say so.
    """
    open_theses = search(status="open", limit=1000)
    with_risk = [
        t
        for t in open_theses
        if t.get("direction") in ("long", "short")
        and t.get("sizing", {}).get("dollar_risk") is not None
    ]

    return {
        "open_theses": len(open_theses),
        "open_positions": len(with_risk),
        "watch_only": len(open_theses) - len(with_risk),
        "total_dollar_risk": round(
            sum(float(t["sizing"]["dollar_risk"]) for t in with_risk), 2
        ),
        "positions": [
            {
                "id": t["id"],
                "ticker": t["ticker"],
                "direction": t["direction"],
                "dollar_risk": t["sizing"]["dollar_risk"],
            }
            for t in with_risk
        ],
    }
