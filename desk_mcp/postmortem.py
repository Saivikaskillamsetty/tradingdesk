"""Scoring the desk's closed calls, so the process can be corrected.

The journal makes calls scoreable. This module scores them. The distinction
matters: a desk that records theses but never grades them has built an archive
of its own reasoning and learned nothing from it, which is a more expensive way
of forgetting.

Two questions are asked here, and they are not the same question:

1. **Did the calls make money?** Expectancy in R, win rate, payoff ratio.
2. **Was the confidence justified?** Whether high-conviction calls actually
   outperformed low-conviction ones, and whether the stated horizon matched the
   holding period. A desk can be profitable and badly calibrated at once --
   that is a desk carrying size on the wrong names and getting rescued by the
   rest.

Everything is computed here rather than by an agent, for the reason every other
number on this desk is: an expectancy figure worked out in a prompt looks
exactly as authoritative when it is wrong.

What cannot be computed is whether a call was right *for the reason given*. A
thesis that worked because of something nobody predicted is a losing process
with a winning outcome, and only the close note knows the difference. Notes are
returned verbatim so they can be read, never summarised into a score.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from desk_mcp import journal

# Below this many scored calls, differences between buckets are noise. Twenty
# is not a statistical threshold so much as an honesty one: it is roughly where
# a single lucky trade stops being able to reverse the sign of expectancy.
MIN_SAMPLE = 20

CONVICTION_ORDER = ("low", "medium", "high")

# Outcomes grouped by what they say about the exit, not about the result.
_EXIT_DISCIPLINE = {
    "target_hit": "plan_ran",
    "stopped_out": "plan_ran",
    "closed_manual": "discretionary",
    "expired": "thesis_lapsed",
    "invalidated": "thesis_lapsed",
}


class PostmortemError(ValueError):
    """A review that cannot be produced as asked."""


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _planned_r(
    entry: float | None, stop: float | None, target: float | None
) -> float | None:
    """Reward-to-risk as it was planned, from the recorded levels.

    Recomputed from entry, stop and target rather than read from the stored
    `reward_risk` field, which was supplied by whoever wrote the thesis and is
    the one number in the record that was never checked.
    """
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if not risk:
        return None
    return round(abs(target - entry) / risk, 2)


def _days_held(created_at: str | None, closed_at: str | None) -> int | None:
    if not created_at or not closed_at:
        return None
    try:
        opened = datetime.fromisoformat(created_at)
        closed = datetime.fromisoformat(closed_at)
    except ValueError:
        return None
    return max((closed - opened).days, 0)


def _unscored_reason(entry: dict[str, Any]) -> str | None:
    """Why a closed thesis produced no R, or None if it produced one.

    Each of these is a different failure and they are kept apart. A watch call
    has no R by design; a position closed without recording the fill has no R
    because the record is incomplete, and that is a process problem.
    """
    if entry.get("direction") == "watch":
        return "watch call — no position was taken, so there is no R to score"

    levels = entry.get("levels") or {}
    if levels.get("entry") is None or levels.get("stop") is None:
        return "no entry or stop recorded, so R cannot be computed"

    outcome = entry.get("outcome") or {}
    if outcome.get("exit_price") is None:
        return "closed without an exit price, so R was never computed"

    if outcome.get("realised_r") is None:
        return "realised R is missing from the record"

    return None


def describe(entry: dict[str, Any]) -> dict[str, Any]:
    """One thesis with the comparisons a review needs, computed.

    Kept separate from the stored record so a review never mutates the journal
    and so the derived numbers are visibly derived.
    """
    levels = entry.get("levels") or {}
    outcome = entry.get("outcome") or {}

    planned = _planned_r(levels.get("entry"), levels.get("stop"), levels.get("target"))
    realised = outcome.get("realised_r")

    capture = None
    if planned is not None and realised is not None and planned:
        capture = round(realised / planned, 2)

    return {
        "id": entry.get("id"),
        "ticker": entry.get("ticker"),
        "status": entry.get("status"),
        "direction": entry.get("direction"),
        "horizon": entry.get("horizon"),
        "conviction": entry.get("conviction"),
        "planned_r": planned,
        "realised_r": realised,
        "r_capture": capture,
        "days_held": _days_held(entry.get("created_at"), entry.get("closed_at")),
        "outcome": outcome.get("result"),
        "exit_discipline": _EXIT_DISCIPLINE.get(outcome.get("result") or ""),
        "unscored_reason": (
            _unscored_reason(entry) if entry.get("status") == "closed" else None
        ),
    }


def _stats(rs: list[float]) -> dict[str, Any]:
    """Performance of a set of scored calls, in R.

    R rather than currency throughout: a 3R win on a small position is a better
    call than a 0.5R win on a large one, and only one of those two facts
    survives conversion to dollars.
    """
    count = len(rs)
    if not count:
        return {
            "count": 0,
            "expectancy_r": None,
            "total_r": None,
            "win_rate": None,
            "wins": 0,
            "losses": 0,
            "scratches": 0,
            "avg_win_r": None,
            "avg_loss_r": None,
            "payoff_ratio": None,
            "best_r": None,
            "worst_r": None,
        }

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    scratches = [r for r in rs if r == 0]

    avg_win = _mean(wins)
    avg_loss = _mean(losses)

    return {
        "count": count,
        # Expectancy is the number that matters. Win rate without it is the
        # statistic every losing strategy quotes.
        "expectancy_r": _mean(rs),
        "total_r": round(sum(rs), 2),
        "win_rate": round(len(wins) / count, 3),
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "payoff_ratio": (
            round(avg_win / abs(avg_loss), 2) if avg_win and avg_loss else None
        ),
        "best_r": max(rs),
        "worst_r": min(rs),
    }


def _bucket(
    scored: list[tuple[dict[str, Any], float]],
    key: Callable[[dict[str, Any]], Any],
) -> dict[str, dict[str, Any]]:
    """Stats grouped by some attribute of the thesis record."""
    groups: dict[str, list[float]] = {}
    for entry, r in scored:
        groups.setdefault(str(key(entry) or "unspecified"), []).append(r)
    return {name: _stats(values) for name, values in sorted(groups.items())}


def _conviction_ordering(by_conviction: dict[str, dict[str, Any]]) -> bool | None:
    """Whether stated conviction actually predicted expectancy.

    This is the calibration question. Conviction is the one input the desk
    supplies from judgement alone, so if high-conviction calls do not out-earn
    low-conviction ones, the judgement is decoration and sizing up on it is
    paying for a signal that is not there.

    Returns None when fewer than two buckets carry data -- with one bucket
    there is no ordering to hold or break.
    """
    present = [
        (name, by_conviction[name]["expectancy_r"])
        for name in CONVICTION_ORDER
        if name in by_conviction and by_conviction[name]["expectancy_r"] is not None
    ]
    if len(present) < 2:
        return None
    values = [value for _, value in present]
    return all(a <= b for a, b in zip(values, values[1:]))


def score_book(
    ticker: str | None = None,
    horizon: str | None = None,
    direction: str | None = None,
    since: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Grade the closed book and report how well-calibrated it was.

    Args:
        ticker: Optional symbol filter.
        horizon: Optional "swing", "positional" or "long_term" filter.
        direction: Optional "long", "short" or "watch" filter.
        since: Optional ISO date; only theses created on or after it.
        limit: Maximum closed theses to read.
    """
    if horizon is not None and horizon not in journal.HORIZONS:
        raise PostmortemError(
            f"horizon must be one of {', '.join(journal.HORIZONS)}; got {horizon!r}"
        )
    if direction is not None and direction not in journal.DIRECTIONS:
        raise PostmortemError(
            f"direction must be one of {', '.join(journal.DIRECTIONS)}; "
            f"got {direction!r}"
        )
    if since is not None:
        try:
            datetime.fromisoformat(since)
        except ValueError as exc:
            raise PostmortemError(
                f"since must be an ISO date such as 2026-01-01; got {since!r}"
            ) from exc

    closed = journal.search(ticker=ticker, status="closed", limit=limit)
    if horizon:
        closed = [t for t in closed if t.get("horizon") == horizon]
    if direction:
        closed = [t for t in closed if t.get("direction") == direction]
    if since:
        closed = [t for t in closed if (t.get("created_at") or "") >= since]

    scored: list[tuple[dict[str, Any], float]] = []
    unscored: list[dict[str, Any]] = []
    watch_outcomes: dict[str, int] = {}
    captures: list[float] = []
    holding_periods: list[int] = []

    for entry in closed:
        detail = describe(entry)
        if entry.get("direction") == "watch":
            result = detail["outcome"] or "unrecorded"
            watch_outcomes[result] = watch_outcomes.get(result, 0) + 1

        reason = detail["unscored_reason"]
        if reason:
            unscored.append(
                {"id": detail["id"], "ticker": detail["ticker"], "reason": reason}
            )
            continue

        scored.append((entry, float(detail["realised_r"])))
        if detail["r_capture"] is not None:
            captures.append(detail["r_capture"])
        if detail["days_held"] is not None:
            holding_periods.append(detail["days_held"])

    overall = _stats([r for _, r in scored])
    by_conviction = _bucket(scored, lambda e: e.get("conviction"))

    limitations = [
        "Only journalled calls are scored. A position taken without recording "
        "a thesis is invisible here, so this is the process as documented "
        "rather than the process as run.",
        "Whether a call was right *for the reason given* is not computable. "
        "Read the close notes; a thesis that worked for an unrelated reason is "
        "a losing process with a winning outcome.",
    ]
    if overall["count"] < MIN_SAMPLE:
        limitations.append(
            f"{overall['count']} scored calls is below the {MIN_SAMPLE} needed "
            f"before differences between buckets mean anything. Treat every "
            f"figure here as descriptive, not predictive."
        )
    if unscored:
        limitations.append(
            f"{len(unscored)} closed theses produced no R and are excluded from "
            f"every performance figure above. They are listed under `unscored` "
            f"with the reason; the ones missing an exit price are a "
            f"record-keeping failure, not a data limitation."
        )
    if watch_outcomes:
        limitations.append(
            "Watch calls carry no R. Whether staying out was correct has to be "
            "checked against what the name did afterwards — the journal cannot "
            "answer it."
        )

    return {
        "filters": {
            "ticker": ticker,
            "horizon": horizon,
            "direction": direction,
            "since": since,
        },
        "closed_theses": len(closed),
        "overall": overall,
        "by_conviction": by_conviction,
        "by_horizon": _bucket(scored, lambda e: e.get("horizon")),
        "by_direction": _bucket(scored, lambda e: e.get("direction")),
        "by_outcome": _bucket(scored, lambda e: (e.get("outcome") or {}).get("result")),
        "calibration": {
            "conviction_ordering_holds": _conviction_ordering(by_conviction),
            "conviction_note": (
                "True when expectancy rises with stated conviction. False means "
                "conviction did not predict outcome, and sizing up on it was "
                "paying for a signal that was not there."
            ),
            "avg_r_capture": _mean(captures),
            "capture_note": (
                "Realised R as a fraction of planned R. Persistently below 1 "
                "means targets are set beyond where positions are actually "
                "exited; persistently above 1 means they are set too near and "
                "winners are being cut."
            ),
            "avg_days_held": _mean([float(d) for d in holding_periods]),
            "scored_sample": overall["count"],
            "minimum_meaningful_sample": MIN_SAMPLE,
        },
        "watch_calls": {
            "closed": sum(watch_outcomes.values()),
            "by_outcome": watch_outcomes,
        },
        "unscored": unscored,
        "limitations": limitations,
    }


def review(thesis_id: str) -> dict[str, Any]:
    """Assemble one call for review, with the plan and the result side by side.

    Returns the thesis as written together with the derived comparisons. It
    deliberately reaches no conclusion: the falsifiers are returned unchecked
    because checking them means going and looking at what the price and the
    filings actually did, which is the reviewer's job and not the journal's.
    """
    entry = journal.load(thesis_id)
    detail = describe(entry)

    limitations: list[str] = []
    if entry.get("status") == "open":
        limitations.append(
            "This thesis is still open. There is no outcome to score — the "
            "useful review here is whether any falsifier has already fired."
        )
    if detail["planned_r"] is None:
        limitations.append(
            "No target was recorded, so planned R and R capture cannot be "
            "computed. The call cannot be judged against its own plan."
        )
    if detail["exit_discipline"] == "discretionary":
        limitations.append(
            "Closed manually, so neither the stop nor the target was tested. "
            "The result reflects a decision taken after the thesis was written."
        )
    if not (entry.get("outcome") or {}).get("note"):
        limitations.append(
            "No close note. Whether the thesis was right for the stated reason "
            "was not recorded and cannot be reconstructed now."
        )
    if entry.get("gaps"):
        limitations.append(
            "The call was made with known gaps, listed under `gaps_at_the_time`. "
            "Judge the decision against what was knowable then, not against "
            "what is knowable now."
        )

    return {
        "thesis": {
            "id": entry.get("id"),
            "ticker": entry.get("ticker"),
            "status": entry.get("status"),
            "created_at": entry.get("created_at"),
            "closed_at": entry.get("closed_at"),
            "text": entry.get("thesis"),
            "direction": entry.get("direction"),
            "horizon": entry.get("horizon"),
            "conviction": entry.get("conviction"),
            "levels": entry.get("levels"),
            "sizing": entry.get("sizing"),
        },
        "derived": {
            "planned_r": detail["planned_r"],
            "realised_r": detail["realised_r"],
            "r_capture": detail["r_capture"],
            "days_held": detail["days_held"],
            "outcome": detail["outcome"],
            "exit_discipline": detail["exit_discipline"],
            "unscored_reason": detail["unscored_reason"],
        },
        # Returned verbatim and unchecked. Whether each one fired is a question
        # for the price and the filings, and answering it from the record alone
        # would be inventing the answer.
        "falsifiers_to_check": list(entry.get("falsifiers") or []),
        "evidence_as_cited": list(entry.get("evidence") or []),
        "gaps_at_the_time": list(entry.get("gaps") or []),
        "close_note": (entry.get("outcome") or {}).get("note") or "",
        "execution": entry.get("execution"),
        "limitations": limitations,
    }
