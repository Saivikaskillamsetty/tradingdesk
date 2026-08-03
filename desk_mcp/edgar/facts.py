"""Resolve XBRL company facts into normalised, provenance-stamped values.

Design rules this module exists to enforce:

1. A line item is resolved by scanning *all* plausible concepts and keeping
   whichever carries the most recent data -- never by trusting one concept.
2. Every returned value states where it came from and what period it covers,
   so an agent can cite it and a human can audit it.
3. Data that is too old to answer the question raises rather than returning
   quietly, because a stale number is worse than no number.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from . import concepts as concept_registry
from .client import EdgarError, company_facts, resolve_cik  # noqa: F401
from .concepts import LineItem, PeriodType

# Annual filings vary either side of 365 days (52/53-week fiscal calendars,
# transition periods). Quarterly likewise.
_ANNUAL_DAYS = (330, 400)
_QUARTERLY_DAYS = (78, 104)

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F"}


class StaleDataError(EdgarError):
    """The freshest fact available is older than the caller can tolerate."""


class MissingDataError(EdgarError):
    """No concept in the candidate chain carried usable data."""


@dataclass(frozen=True)
class Fact:
    """A single financial value with full provenance.

    `fiscal_year` is derived from `period_end`, not copied from EDGAR's `fy`
    field. EDGAR's `fy`/`fp` describe the filing a fact was *reported in*, not
    the period it covers -- a 10-K restates two prior years, so its FY2021
    comparative row is tagged fy=2023. Those raw values are preserved as
    `reported_in_fy`/`reported_in_fp` for auditing.
    """

    line_item: str
    label: str
    value: float
    unit: str
    concept: str
    period_start: str | None
    period_end: str
    fiscal_year: int
    fiscal_period: str
    form: str
    accession: str
    filed: str
    reported_in_fy: int | None
    reported_in_fp: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _duration_ok(row: dict[str, Any], bounds: tuple[int, int]) -> bool:
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return False
    days = (_parse(end) - _parse(start)).days
    return bounds[0] <= days <= bounds[1]


def _candidate_rows(
    facts: dict[str, Any], item: LineItem, period: str
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """Yield (concept, unit, row) for every row matching the item and period."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})

    for concept in item.concepts:
        entry = gaap.get(concept) or dei.get(concept)
        if not entry:
            continue

        for unit in item.units:
            for row in entry.get("units", {}).get(unit, []):
                if "end" not in row or row.get("val") is None:
                    continue

                if item.period_type is PeriodType.INSTANT:
                    # Balance-sheet facts are points in time and carry no start.
                    if "start" in row:
                        continue
                else:
                    bounds = _ANNUAL_DAYS if period == "annual" else _QUARTERLY_DAYS
                    if not _duration_ok(row, bounds):
                        continue

                if period == "annual" and row.get("form") not in _ANNUAL_FORMS:
                    continue

                yield concept, unit, row


def _period_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row.get("start") or "", row["end"])


def _fiscal_year(period_end: str) -> int:
    """Fiscal year a period belongs to, by the company's own convention.

    Companies name a fiscal year after the calendar year it ends in: Apple's
    year ending 2025-09-27 is FY2025, NVIDIA's ending 2026-01-25 is FY2026.
    The exception is a 52/53-week calendar whose year-end drifts a few days
    into January -- that belongs to the prior year.
    """
    end = _parse(period_end)
    if end.month == 1 and end.day <= 7:
        return end.year - 1
    return end.year


def _fiscal_period(row: dict[str, Any], item: LineItem, period: str) -> str:
    if period == "annual":
        return "FY"
    # EDGAR's fp is reliable for the quarter a 10-Q covers.
    reported = row.get("fp") or ""
    return reported if reported.startswith("Q") else "Q?"


def series(
    ticker: str, line_item: str, period: str = "annual", limit: int = 12
) -> list[Fact]:
    """Historical values for a line item, oldest to newest.

    Where several concepts report the same period, the most recently *filed*
    row wins -- that is the restated figure the company now stands behind.
    """
    if period not in ("annual", "quarterly"):
        raise ValueError(f"period must be 'annual' or 'quarterly', got {period!r}")

    item = concept_registry.get(line_item)
    facts = company_facts(resolve_cik(ticker))

    # Collapse to one row per reporting period, keeping the freshest filing.
    best: dict[tuple[str, str], tuple[str, str, dict[str, Any]]] = {}
    for concept, unit, row in _candidate_rows(facts, item, period):
        key = _period_key(row)
        incumbent = best.get(key)
        if incumbent is None or row.get("filed", "") > incumbent[2].get("filed", ""):
            best[key] = (concept, unit, row)

    if not best:
        raise MissingDataError(
            f"{ticker}: no {period} data for {line_item!r} across concepts "
            f"{', '.join(item.concepts)}"
        )

    facts_out = [
        Fact(
            line_item=item.key,
            label=item.label,
            value=float(row["val"]),
            unit=unit,
            concept=concept,
            period_start=row.get("start"),
            period_end=row["end"],
            fiscal_year=_fiscal_year(row["end"]),
            fiscal_period=_fiscal_period(row, item, period),
            form=row.get("form", ""),
            accession=row.get("accn", ""),
            filed=row.get("filed", ""),
            reported_in_fy=row.get("fy"),
            reported_in_fp=row.get("fp"),
        )
        for concept, unit, row in best.values()
    ]

    facts_out.sort(key=lambda f: f.period_end)
    return facts_out[-limit:]


def latest(
    ticker: str,
    line_item: str,
    period: str = "annual",
    max_age_days: int | None = 500,
) -> Fact:
    """The most recent value for a line item.

    `max_age_days` defaults to 500 for annual data: a company more than ~16
    months past its last annual report has either gone dark or changed its
    reporting, and either way the number should not be used silently.
    """
    history = series(ticker, line_item, period=period, limit=1)
    fact = history[-1]

    if max_age_days is not None:
        age = (date.today() - _parse(fact.period_end)).days
        if age > max_age_days:
            raise StaleDataError(
                f"{ticker}: freshest {line_item!r} is period ending "
                f"{fact.period_end} ({age} days old, limit {max_age_days}). "
                f"Concept {fact.concept!r} filed {fact.filed}."
            )

    return fact


def statement(
    ticker: str, period: str = "annual", max_age_days: int | None = 500
) -> dict[str, Any]:
    """Every known line item for a ticker, with per-item errors surfaced.

    A missing line item is reported rather than raised: plenty of companies
    legitimately lack inventory or R&D, and one absent item should not sink
    the whole statement.

    Company-level failures are different and do raise. Tolerating them would
    return an empty-but-successful-looking statement, which reads as "this
    company reports nothing" rather than "this ticker does not exist".
    """
    # Resolve the company first so a bad ticker fails loudly and once,
    # instead of seventeen times inside the loop below.
    cik = resolve_cik(ticker)
    company = company_facts(cik)
    name = company.get("entityName", "")

    values: dict[str, Any] = {}
    problems: dict[str, str] = {}

    for key in concept_registry.ALL_KEYS:
        try:
            values[key] = latest(
                ticker, key, period=period, max_age_days=max_age_days
            ).to_dict()
        except EdgarError as exc:
            problems[key] = str(exc)

    if not values:
        raise MissingDataError(
            f"{ticker.upper()} (CIK {cik}): no {period} data for any line item. "
            f"The company may file in a non-US-GAAP taxonomy, or may not have "
            f"filed recently."
        )

    return {
        "ticker": ticker.upper(),
        "company_name": name,
        "cik": cik,
        "period": period,
        "retrieved_at": date.today().isoformat(),
        "facts": values,
        "unavailable": problems,
    }
