"""Filing history and insider transactions from EDGAR submissions."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .client import resolve_cik, submissions

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{doc}"

# Forms worth surfacing to a research agent, with what they signal.
FORM_MEANING = {
    "10-K": "annual report",
    "10-Q": "quarterly report",
    "8-K": "material event",
    "4": "insider transaction",
    "SC 13D": "activist stake (>5%)",
    "SC 13G": "passive stake (>5%)",
    "DEF 14A": "proxy statement",
    "S-1": "registration / IPO",
    "424B5": "securities offering",
}


@dataclass(frozen=True)
class Filing:
    form: str
    meaning: str
    filed: str
    period: str | None
    accession: str
    primary_document: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build(cik: int, recent: dict[str, list[Any]], index: int) -> Filing:
    accession = recent["accessionNumber"][index]
    document = recent["primaryDocument"][index]
    form = recent["form"][index]

    return Filing(
        form=form,
        meaning=FORM_MEANING.get(form, ""),
        filed=recent["filingDate"][index],
        period=recent["reportDate"][index] or None,
        accession=accession,
        primary_document=document,
        url=ARCHIVE_URL.format(
            cik=cik,
            accession_nodash=accession.replace("-", ""),
            doc=document,
        ),
    )


def recent_filings(
    ticker: str, forms: list[str] | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Most recent filings, newest first, optionally filtered by form type."""
    cik = resolve_cik(ticker)
    recent = submissions(cik)["filings"]["recent"]
    wanted = {f.upper() for f in forms} if forms else None

    results: list[dict[str, Any]] = []
    for i, form in enumerate(recent["form"]):
        if wanted and form.upper() not in wanted:
            continue
        results.append(_build(cik, recent, i).to_dict())
        if len(results) >= limit:
            break

    return results


def insider_activity(ticker: str, limit: int = 25) -> dict[str, Any]:
    """Recent Form 4 filings -- insider buys and sells.

    Returns filing metadata and links rather than parsed transaction amounts;
    Form 4 bodies are per-filing XML and are fetched on demand by the agent
    when a specific transaction matters.
    """
    filings = recent_filings(ticker, forms=["4", "4/A"], limit=limit)

    return {
        "ticker": ticker.upper(),
        "form_4_count": len(filings),
        "filings": filings,
        "note": (
            "Form 4 signals timing and direction of insider activity. "
            "Fetch a filing URL to read transaction size, price and role. "
            "Scheduled 10b5-1 sales carry far less signal than open-market buys."
        ),
    }
