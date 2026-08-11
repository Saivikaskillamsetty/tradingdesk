"""Whether the desk's own record-keeping can still be trusted.

Every other module answers a question about a company. This one answers a
question about the desk: is the book complete, are its numbers still
computable, and are the data sources it depends on actually available.

It exists because the failures that matter most here are silent. A thesis
closed without an exit price does not raise anything -- it simply drops out of
every performance figure forever. An open swing call from eight months ago is
not an error, it is a position nobody remembered to resolve, and it goes on
consuming portfolio heat while it sits there. A missing API key surfaces as a
tool error deep inside an analysis, long after the agent has committed to a
line of reasoning.

None of this is a market signal. It is the desk checking its own instruments.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from desk_mcp import cache, journal

# How long an open thesis can sit before its horizon has plainly elapsed. A
# swing call still open after three months was not a swing call; whatever it
# was, the record no longer describes it.
STALE_AFTER_DAYS = {
    "swing": 60,
    "positional": 180,
    "long_term": 730,
}

CREDENTIALS = {
    "ALPACA_API_KEY": "prices, technicals, screener, paper execution",
    "ALPACA_SECRET_KEY": "prices, technicals, screener, paper execution",
    "FRED_API_KEY": "macro only — everything else runs without it",
    "SEC_USER_AGENT": "EDGAR courtesy header; defaults if unset",
}

# EDGAR needs no key, so its absence from the credential list is not an
# oversight. It is the one source that cannot be misconfigured.
KEYLESS_SOURCES = ("SEC EDGAR", "House Clerk disclosures")


def _age_days(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - moment).days, 0)


def _journal_integrity() -> dict[str, Any]:
    """Problems in the book that no single tool call would surface."""
    directory = journal.theses_dir()
    unreadable: list[dict[str, str]] = []
    entries: list[dict[str, Any]] = []

    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                entries.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as exc:
                # `list_theses` skips these silently by design so one bad file
                # cannot break the book. Silently is fine there and not here.
                unreadable.append({"file": path.name, "error": str(exc)})

    open_theses = [e for e in entries if e.get("status") == "open"]
    closed = [e for e in entries if e.get("status") == "closed"]

    stale: list[dict[str, Any]] = []
    for entry in open_theses:
        age = _age_days(entry.get("created_at"))
        limit = STALE_AFTER_DAYS.get(entry.get("horizon") or "", 180)
        if age is not None and age > limit:
            stale.append(
                {
                    "id": entry.get("id"),
                    "ticker": entry.get("ticker"),
                    "direction": entry.get("direction"),
                    "horizon": entry.get("horizon"),
                    "age_days": age,
                    "stale_after_days": limit,
                }
            )

    unscoreable = [
        {
            "id": entry.get("id"),
            "ticker": entry.get("ticker"),
            "outcome": (entry.get("outcome") or {}).get("result"),
        }
        for entry in closed
        if entry.get("direction") in ("long", "short")
        and (entry.get("outcome") or {}).get("exit_price") is None
    ]

    missing_notes = [
        entry.get("id")
        for entry in closed
        if not (entry.get("outcome") or {}).get("note")
    ]

    unsized = [
        {"id": entry.get("id"), "ticker": entry.get("ticker")}
        for entry in open_theses
        if entry.get("direction") in ("long", "short")
        and (entry.get("sizing") or {}).get("dollar_risk") is None
    ]

    return {
        "directory": str(directory),
        "total_theses": len(entries),
        "open": len(open_theses),
        "closed": len(closed),
        "unreadable_files": unreadable,
        "stale_open_theses": sorted(stale, key=lambda r: -r["age_days"]),
        "closed_without_exit_price": unscoreable,
        "closed_without_note": missing_notes,
        "open_positions_without_sizing": unsized,
    }


def _credentials() -> dict[str, Any]:
    """Which keys are present. Presence only -- never the value, never a prefix."""
    present, missing = {}, {}
    for name, used_for in CREDENTIALS.items():
        if os.environ.get(name, "").strip():
            present[name] = used_for
        else:
            missing[name] = used_for
    return {
        "present": sorted(present),
        "missing": [{"variable": k, "blocks": v} for k, v in missing.items()],
        "keyless_sources": list(KEYLESS_SOURCES),
        "note": (
            "Presence only. Values are never read into a response — run "
            "`uv run python scripts/check_keys.py` for a masked check."
        ),
    }


def _cache_state() -> dict[str, Any]:
    directory = cache.CACHE_DIR
    if not directory.exists():
        return {
            "directory": str(directory),
            "entries": 0,
            "note": "Nothing cached yet; the first calls of a session will be slower.",
        }

    files = list(directory.glob("*.json"))
    total_bytes = sum(f.stat().st_size for f in files)
    newest = max((f.stat().st_mtime for f in files), default=None)
    oldest = min((f.stat().st_mtime for f in files), default=None)

    def age(mtime: float | None) -> int | None:
        if mtime is None:
            return None
        return max(int((datetime.now().timestamp() - mtime) // 86_400), 0)

    return {
        "directory": str(directory),
        "entries": len(files),
        "size_mb": round(total_bytes / 1_048_576, 2),
        "newest_entry_age_days": age(newest),
        "oldest_entry_age_days": age(oldest),
        "note": (
            "Filed documents are cached permanently and are safe to keep. "
            "Deleting the directory costs rate-limit budget, never correctness."
        ),
    }


def desk_health() -> dict[str, Any]:
    """The state of the desk's own record-keeping and dependencies.

    Runs offline. Credentials are checked for presence, not validity — a key
    that exists but has been revoked looks healthy here and will fail at the
    call site.
    """
    integrity = _journal_integrity()
    credentials = _credentials()
    cache_state = _cache_state()

    findings: list[dict[str, str]] = []

    if integrity["unreadable_files"]:
        findings.append(
            {
                "severity": "error",
                "finding": (
                    f"{len(integrity['unreadable_files'])} thesis files cannot be "
                    f"parsed. `list_theses` skips them silently, so the book is "
                    f"quietly incomplete and portfolio heat is understated."
                ),
            }
        )
    if integrity["closed_without_exit_price"]:
        findings.append(
            {
                "severity": "error",
                "finding": (
                    f"{len(integrity['closed_without_exit_price'])} closed "
                    f"positions carry no exit price, so realised R was never "
                    f"computed. They are permanently excluded from every "
                    f"figure `score_book` reports."
                ),
            }
        )
    if integrity["open_positions_without_sizing"]:
        findings.append(
            {
                "severity": "error",
                "finding": (
                    f"{len(integrity['open_positions_without_sizing'])} open "
                    f"positions carry no dollar risk, so they contribute "
                    f"nothing to portfolio heat while carrying real exposure. "
                    f"Heat is understated by an unknown amount."
                ),
            }
        )
    if integrity["stale_open_theses"]:
        oldest = integrity["stale_open_theses"][0]
        findings.append(
            {
                "severity": "warning",
                "finding": (
                    f"{len(integrity['stale_open_theses'])} open theses have "
                    f"outlived their horizon, the oldest by "
                    f"{oldest['age_days'] - oldest['stale_after_days']} days "
                    f"({oldest['ticker']}). Each one still counts against heat."
                ),
            }
        )
    if integrity["closed_without_note"]:
        findings.append(
            {
                "severity": "warning",
                "finding": (
                    f"{len(integrity['closed_without_note'])} closed theses "
                    f"have no note, so whether they worked for the stated "
                    f"reason can no longer be reconstructed."
                ),
            }
        )
    for missing in credentials["missing"]:
        findings.append(
            {
                "severity": "warning",
                "finding": (
                    f"{missing['variable']} is not set — blocks: {missing['blocks']}."
                ),
            }
        )

    if not findings:
        findings.append(
            {"severity": "ok", "finding": "No record-keeping problems found."}
        )

    severities = {f["severity"] for f in findings}
    status = (
        "error" if "error" in severities
        else "warning" if "warning" in severities
        else "ok"
    )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "findings": findings,
        "journal": integrity,
        "credentials": credentials,
        "cache": cache_state,
        "limitations": [
            "Offline. No data source is contacted, so a reachable-but-broken "
            "API looks healthy here.",
            "Credentials are checked for presence, not validity. A revoked key "
            "passes this check and fails at the call site.",
            "The journal is the only book this can see. A position held at the "
            "broker without a thesis is invisible — `reconcile_positions` is "
            "the check that catches those, and it needs broker credentials.",
        ],
    }
