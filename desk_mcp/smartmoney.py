"""What the people with better information than us are actually doing.

Three separate populations, three different signals, and conflating them is
the main way this data gets misread:

**Institutions (13F-HR).** Managers over $100M report their US equity book
quarterly. The filing is XML, official, and free. It is also *stale by up to
45 days* at publication and covers only long US equity -- no shorts, no
options exposure beyond puts and calls listed as such, no cash, no foreign
listings. A 13F is a photograph of one side of a book, developed six weeks
late.

**Insiders (Form 4).** Officers and directors, filed within two business days.
Far timelier than a 13F, and the only one of the three where the filer is
legally presumed to know something. Handled by `edgar.filings.insider_activity`.

**Congress (Periodic Transaction Reports).** Members disclose trades within
45 days under the STOCK Act. The House Clerk publishes an annual XML index of
who filed, but the transactions themselves exist only inside per-filing PDFs,
so they are parsed out of extracted text here. Amounts are reported as
statutory *ranges*, never exact figures, and a range is what this module
returns -- an agent that reports "$15,000" from a "$1,001 - $15,000" band has
invented a number.

Nothing here is a trading signal on its own. A 13F increase from six weeks ago
and a congressional sale disclosed at the legal limit are both history.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import httpx
from pypdf import PdfReader

from desk_mcp import cache
from desk_mcp.edgar.client import SEC_UA, EdgarError, fetch_json, fetch_text, submissions

COMPANY_SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/index.json"
FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{doc}"

HOUSE_INDEX_URL = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
)
HOUSE_PTR_URL = (
    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
)

# Periodic Transaction Report. The only House filing type that carries trades;
# the rest are annual disclosures, extensions and candidate paperwork.
PTR = "P"

_INFO_TABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

TRANSACTION_MEANING = {
    "P": "purchase",
    "S": "sale",
    "S (partial)": "partial sale",
    "E": "exchange",
}

# One row of a PTR table as pypdf lays it out: the asset line carries the
# ticker in parentheses and a bracketed asset class, then the transaction
# letter, two run-together dates, and the statutory amount band.
_PTR_ROW = re.compile(
    r"\((?P<ticker>[A-Z][A-Z0-9.\-]{0,5})\)\s*\[(?P<asset_class>[A-Z]{2,4})\]\s*"
    r"(?P<transaction>[PSE](?:\s*\(partial\))?)\s*"
    r"(?P<transaction_date>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<notification_date>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<amount_range>\$[\d,]+\s*-\s*\$[\d,]+)",
    re.MULTILINE,
)


class SmartMoneyError(RuntimeError):
    """Ownership data could not be retrieved or read as filed."""


def _http_get(url: str, timeout: float = 45.0) -> httpx.Response:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SmartMoneyError(f"request to {url} failed: {exc}") from exc
    if response.status_code != 200:
        raise SmartMoneyError(f"{url} returned HTTP {response.status_code}")
    return response


# --------------------------------------------------------------------------
# Institutions: 13F-HR
# --------------------------------------------------------------------------


def resolve_institution(name_or_cik: str) -> dict[str, Any]:
    """Find a 13F filer's CIK by name, or accept one directly.

    Resolved through EDGAR rather than a hardcoded table of managers. A
    remembered CIK that is off by a digit resolves to a real but different
    filer, and every holding returned afterwards looks entirely plausible.
    """
    query = name_or_cik.strip()
    if not query:
        raise SmartMoneyError("an institution name or CIK is required")

    if query.isdigit():
        cik = int(query)
        try:
            name = submissions(cik).get("name") or f"CIK {cik}"
        except EdgarError as exc:
            raise SmartMoneyError(f"no EDGAR filer with CIK {cik}") from exc
        return {"cik": cik, "name": name, "resolved_from": "cik"}

    response = _http_get(
        f"{COMPANY_SEARCH_URL}?action=getcompany&company={query.replace(' ', '+')}"
        f"&type=13F-HR&dateb=&owner=include&count=20&output=atom"
    )
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise SmartMoneyError(f"EDGAR company search for {query!r} was unreadable") from exc

    # The feed carries a default namespace, so every element in it is
    # namespaced -- including the unprefixed-looking `company-info`.
    atom = "{http://www.w3.org/2005/Atom}"

    def child_text(parent: ET.Element, tag: str) -> str:
        found = parent.find(f"{atom}{tag}")
        if found is None:
            found = parent.find(tag)
        return (found.text or "").strip() if found is not None else ""

    def info_node(parent: ET.Element) -> ET.Element:
        found = parent.find(f"{atom}company-info")
        if found is None:
            found = parent.find("company-info")
        return parent if found is None else found

    # A single unambiguous match returns the filer directly rather than a list.
    info = info_node(root)
    if info is not root and child_text(info, "cik").isdigit():
        return {
            "cik": int(child_text(info, "cik")),
            "name": child_text(info, "conformed-name") or query,
            "resolved_from": "exact name match",
        }

    candidates = []
    for entry in root.findall(f"{atom}entry"):
        found = info_node(entry)
        cik_text = child_text(found, "cik")
        if cik_text.isdigit():
            candidates.append(
                {
                    "cik": int(cik_text),
                    "name": child_text(found, "conformed-name"),
                }
            )

    if not candidates:
        raise SmartMoneyError(
            f"no 13F filer matching {query!r}. Search the manager's legal entity "
            f"name — funds file under names that differ from how they are known."
        )
    if len(candidates) > 1:
        listed = "; ".join(f"{c['name']} (CIK {c['cik']})" for c in candidates[:8])
        raise SmartMoneyError(
            f"{query!r} matches {len(candidates)} filers: {listed}. "
            f"Call again with the CIK of the one you meant."
        )
    return {**candidates[0], "resolved_from": "single search match"}


def _thirteen_f_filings(cik: int, limit: int = 8) -> list[dict[str, Any]]:
    """13F-HR filings for a CIK, newest first."""
    recent = submissions(cik)["filings"]["recent"]
    found = []
    for i, form in enumerate(recent["form"]):
        if form.upper() != "13F-HR":
            continue
        found.append(
            {
                "accession": recent["accessionNumber"][i],
                "filed": recent["filingDate"][i],
                "period": recent["reportDate"][i] or None,
            }
        )
        if len(found) >= limit:
            break
    return found


def _info_table(cik: int, accession: str) -> list[dict[str, Any]]:
    """Holdings from a 13F filing's information table.

    The table document is not the filing's primary document -- that is the
    cover page -- so the filing index is read to find it.
    """
    nodash = accession.replace("-", "")
    index = fetch_json(
        FILING_INDEX_URL.format(cik=cik, nodash=nodash),
        f"13f_index_{accession}",
        ttl=-1,
    )
    names = [item["name"] for item in index["directory"]["item"]]
    tables = [
        n
        for n in names
        if n.lower().endswith(".xml") and "primary_doc" not in n.lower()
    ]
    if not tables:
        raise SmartMoneyError(
            f"13F {accession} carries no information table; it may be a "
            f"holdings-report notice that defers to another manager's filing."
        )

    body = fetch_text(
        FILING_DOC_URL.format(cik=cik, nodash=nodash, doc=tables[0]),
        f"13f_table_{accession}",
    )
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SmartMoneyError(f"13F {accession} information table is not valid XML") from exc

    # Namespaced or not, depending on the filing agent that produced it.
    def child(parent: ET.Element, tag: str) -> ET.Element | None:
        found = parent.find(f"{_INFO_TABLE_NS}{tag}")
        return parent.find(tag) if found is None else found

    def text(parent: ET.Element, tag: str) -> str:
        found = child(parent, tag)
        # Explicitly against None: an Element with no children is falsy, so a
        # truthiness test here silently drops populated but childless tags.
        return (found.text or "").strip() if found is not None else ""

    nodes = root.findall(f"{_INFO_TABLE_NS}infoTable")
    if not nodes:
        nodes = root.findall("infoTable")

    rows = []
    for node in nodes:
        amount = child(node, "shrsOrPrnAmt")
        shares_text = text(amount, "sshPrnamt") if amount is not None else ""
        rows.append(
            {
                "issuer": text(node, "nameOfIssuer"),
                "cusip": text(node, "cusip"),
                "class": text(node, "titleOfClass"),
                "value": float(text(node, "value") or 0),
                "shares": float(shares_text or 0),
                "put_call": text(node, "putCall") or None,
            }
        )
    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse a 13F table to one line per security.

    A manager with several sub-advisers files the same issuer once per manager.
    Reading the table row-wise reports Berkshire as holding Ally three separate
    times, each at a third of the real position.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        # Puts and calls are a different position from the stock and must not
        # be summed into it.
        key = f"{row['cusip']}|{row['put_call'] or 'SH'}"
        entry = merged.setdefault(
            key,
            {
                "issuer": row["issuer"],
                "cusip": row["cusip"],
                "class": row["class"],
                "put_call": row["put_call"],
                "value": 0.0,
                "shares": 0.0,
                "reported_lines": 0,
            },
        )
        entry["value"] += row["value"]
        entry["shares"] += row["shares"]
        entry["reported_lines"] += 1
    return merged


def _position(entry: dict[str, Any], portfolio_value: float) -> dict[str, Any]:
    shares = entry["shares"]
    return {
        "issuer": entry["issuer"],
        "cusip": entry["cusip"],
        "class": entry["class"],
        "put_call": entry["put_call"],
        "value_usd": round(entry["value"], 2),
        "shares": round(shares, 2),
        "portfolio_weight_pct": (
            round(100 * entry["value"] / portfolio_value, 2) if portfolio_value else None
        ),
        # A self-check the reader can apply: a price per share far outside the
        # plausible range means the filing reported value in thousands, which
        # is how pre-2023 13Fs were filed.
        "implied_price_per_share": round(entry["value"] / shares, 2) if shares else None,
        "reported_lines": entry["reported_lines"],
    }


def holdings(institution: str, quarters: int = 2) -> dict[str, Any]:
    """A manager's latest 13F book, and what changed since the prior quarter.

    Args:
        institution: Manager name or CIK.
        quarters: How many 13F periods to read. 2 gives the latest plus the
            comparison quarter the changes are computed against.
    """
    if quarters < 1:
        raise SmartMoneyError("quarters must be at least 1")

    filer = resolve_institution(institution)
    cik = filer["cik"]
    found = _thirteen_f_filings(cik, limit=max(quarters, 2))
    if not found:
        raise SmartMoneyError(
            f"{filer['name']} (CIK {cik}) has filed no 13F-HR. Managers below "
            f"the $100M reporting threshold do not file one."
        )

    latest = found[0]
    current = _aggregate(_info_table(cik, latest["accession"]))
    portfolio_value = sum(e["value"] for e in current.values())

    positions = sorted(
        (_position(e, portfolio_value) for e in current.values()),
        key=lambda p: p["value_usd"],
        reverse=True,
    )

    changes: dict[str, Any] | None = None
    prior_meta = found[1] if len(found) > 1 else None
    if prior_meta:
        prior = _aggregate(_info_table(cik, prior_meta["accession"]))
        changes = _diff(current, prior, prior_meta)

    filed = latest["filed"]
    period = latest["period"]
    lag_days = None
    if period and filed:
        try:
            lag_days = (
                datetime.fromisoformat(filed) - datetime.fromisoformat(period)
            ).days
        except ValueError:
            lag_days = None

    return {
        "institution": filer["name"],
        "cik": cik,
        "resolved_from": filer["resolved_from"],
        "period": period,
        "filed": filed,
        "reporting_lag_days": lag_days,
        "accession": latest["accession"],
        "portfolio_value_usd": round(portfolio_value, 2),
        "position_count": len(positions),
        "positions": positions,
        "changes": changes,
        "limitations": [
            f"A 13F reports holdings as of {period}, filed {filed}"
            + (f" — {lag_days} days later." if lag_days is not None else ".")
            + " The book has moved since; this is history, not a current position.",
            "Long US-listed equity only. Short positions, cash, bonds, foreign "
            "listings and most derivatives never appear, so portfolio weights "
            "are weights within the reported slice, not within the real fund.",
            "Values are as filed. Filings before 2023 reported value in "
            "thousands rather than dollars — check `implied_price_per_share` "
            "against a plausible share price before quoting a dollar figure.",
        ],
    }


def _diff(
    current: dict[str, dict[str, Any]],
    prior: dict[str, dict[str, Any]],
    prior_meta: dict[str, Any],
) -> dict[str, Any]:
    """What the manager bought, sold, opened and exited between two filings.

    Share counts drive this, not values: a position whose value fell while the
    share count held flat was not sold, it was marked down, and reporting that
    as a reduction is simply wrong.
    """
    opened, exited, increased, reduced = [], [], [], []

    for key, entry in current.items():
        before = prior.get(key)
        if before is None:
            opened.append(
                {
                    "issuer": entry["issuer"],
                    "cusip": entry["cusip"],
                    "class": entry["class"],
                    "shares": round(entry["shares"], 2),
                    "value_usd": round(entry["value"], 2),
                }
            )
            continue
        delta = entry["shares"] - before["shares"]
        if not delta:
            continue
        row = {
            "issuer": entry["issuer"],
            "cusip": entry["cusip"],
            "class": entry["class"],
            "shares_before": round(before["shares"], 2),
            "shares_after": round(entry["shares"], 2),
            "share_change": round(delta, 2),
            "share_change_pct": (
                round(100 * delta / before["shares"], 2) if before["shares"] else None
            ),
        }
        (increased if delta > 0 else reduced).append(row)

    for key, before in prior.items():
        if key not in current:
            exited.append(
                {
                    "issuer": before["issuer"],
                    "cusip": before["cusip"],
                    "class": before["class"],
                    "shares_held": round(before["shares"], 2),
                }
            )

    return {
        "compared_against": {
            "period": prior_meta.get("period"),
            "filed": prior_meta.get("filed"),
            "accession": prior_meta.get("accession"),
        },
        "opened": sorted(opened, key=lambda r: r["value_usd"], reverse=True),
        "exited": sorted(exited, key=lambda r: r["shares_held"], reverse=True),
        "increased": sorted(
            increased, key=lambda r: r["share_change"], reverse=True
        ),
        "reduced": sorted(reduced, key=lambda r: r["share_change"]),
        "note": (
            "Computed from share counts, not values, so a position marked down "
            "by the market is not reported as a sale."
        ),
    }


# --------------------------------------------------------------------------
# Congress: Periodic Transaction Reports
# --------------------------------------------------------------------------


def _house_index(year: int) -> list[dict[str, str]]:
    """The House Clerk's annual filing index, PTR rows only."""
    key = f"house_fd_index_{year}"
    cached = cache.read(key, ttl=6 * 3600)
    if cached is not None:
        return cached

    response = _http_get(HOUSE_INDEX_URL.format(year=year))
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(archive.read(name).decode("utf-8-sig", "replace"))
    except (zipfile.BadZipFile, StopIteration, ET.ParseError) as exc:
        raise SmartMoneyError(
            f"House disclosure index for {year} could not be read: {exc}"
        ) from exc

    rows = [
        {
            "member": " ".join(
                part
                for part in (
                    member.findtext("Prefix"),
                    member.findtext("First"),
                    member.findtext("Last"),
                    member.findtext("Suffix"),
                )
                if part and part.strip()
            ),
            "state_district": (member.findtext("StateDst") or "").strip(),
            "filed": (member.findtext("FilingDate") or "").strip(),
            "doc_id": (member.findtext("DocID") or "").strip(),
            "year": str(year),
        }
        for member in root
        if (member.findtext("FilingType") or "").strip() == PTR
    ]

    cache.write(key, rows)
    return rows


def _ptr_text(year: int, doc_id: str) -> str:
    """Extracted text of one Periodic Transaction Report.

    Cached forever: a filed PDF never changes, and re-extracting one costs a
    download and a parse for a document already read.
    """
    key = f"house_ptr_{year}_{doc_id}"
    cached = cache.read(key, ttl=-1)
    if cached is not None:
        return cached

    response = _http_get(HOUSE_PTR_URL.format(year=year, doc_id=doc_id))
    try:
        reader = PdfReader(io.BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf raises a wide range on malformed files
        raise SmartMoneyError(f"PTR {doc_id} could not be read: {exc}") from exc

    # Header glyphs come through as NULs on these forms; they are decoration.
    text = text.replace("\x00", "")
    cache.write(key, text)
    return text


def _parse_ptr(text: str) -> list[dict[str, Any]]:
    """Transactions out of extracted PTR text.

    Filed as a PDF, so there is no structured source behind these rows -- they
    are read out of a rendered table. Every row keeps the document link so a
    figure that matters can be checked against the filing.
    """
    found = []
    for match in _PTR_ROW.finditer(text):
        transaction = re.sub(r"\s+", " ", match.group("transaction")).strip()
        found.append(
            {
                "ticker": match.group("ticker"),
                "asset_class": match.group("asset_class"),
                "transaction": transaction,
                "transaction_meaning": TRANSACTION_MEANING.get(transaction, "unknown"),
                "transaction_date": match.group("transaction_date"),
                "notification_date": match.group("notification_date"),
                "amount_range": re.sub(r"\s+", " ", match.group("amount_range")),
            }
        )
    return found


def congress_trades(
    ticker: str | None = None,
    member: str | None = None,
    year: int | None = None,
    max_reports: int = 40,
) -> dict[str, Any]:
    """Congressional stock trades disclosed under the STOCK Act.

    Args:
        ticker: Optional symbol filter, applied to parsed transactions.
        member: Optional case-insensitive substring of the member's name.
        year: Disclosure year. Defaults to the current one.
        max_reports: Reports to open and parse, newest filing date first.
            Each is a separate PDF download on first read.
    """
    year = year or datetime.now().year
    index = _house_index(year)

    if member:
        needle = member.strip().lower()
        index = [row for row in index if needle in row["member"].lower()]

    def filed_key(row: dict[str, str]) -> tuple[int, int, int]:
        try:
            month, day, yr = (int(p) for p in row["filed"].split("/"))
            return (yr, month, day)
        except (ValueError, AttributeError):
            return (0, 0, 0)

    index.sort(key=filed_key, reverse=True)
    selected = index[: max(max_reports, 0)]

    wanted = ticker.strip().upper() if ticker else None
    trades: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []

    for row in selected:
        url = HOUSE_PTR_URL.format(year=year, doc_id=row["doc_id"])
        try:
            parsed = _parse_ptr(_ptr_text(year, row["doc_id"]))
        except SmartMoneyError as exc:
            unreadable.append({**row, "url": url, "reason": str(exc)})
            continue

        if not parsed:
            # Scanned and handwritten filings extract to nothing usable. They
            # are reported rather than dropped: a member whose filings cannot
            # be read is not a member who did not trade.
            unreadable.append(
                {
                    **row,
                    "url": url,
                    "reason": "no transactions could be read from the PDF; "
                    "it is likely a scan rather than a digital filing",
                }
            )
            continue

        for trade in parsed:
            if wanted and trade["ticker"] != wanted:
                continue
            trades.append(
                {
                    "member": row["member"],
                    "state_district": row["state_district"],
                    "disclosed": row["filed"],
                    **trade,
                    "source_url": url,
                }
            )

    return {
        "year": year,
        "filters": {"ticker": wanted, "member": member},
        "ptr_filings_total": len(index),
        "ptr_filings_read": len(selected),
        "trades": trades,
        "unreadable_filings": unreadable,
        "limitations": [
            f"{len(selected)} of {len(index)} {year} Periodic Transaction "
            f"Reports were opened. A ticker absent from these results may sit "
            f"in one that was not read — raise `max_reports` before concluding "
            f"nobody traded it.",
            "Amounts are the statutory bands members report, not figures. "
            "Quote the range; there is no exact number behind it.",
            "Disclosure is due within 45 days of the trade, so a transaction "
            "date here can be far older than the date it was disclosed. Late "
            "filing is common and carries a nominal penalty.",
            "Parsed from PDF text, with no structured source behind it. Follow "
            "`source_url` before quoting a specific transaction.",
            "House filings only. The Senate publishes separately and is not "
            "covered here, so an absence is not evidence of no congressional "
            "trading.",
            "A member's trade is frequently placed by an outside manager with "
            "no direction from them at all — the filings themselves say so.",
        ],
    }


def congress_activity_by_ticker(
    year: int | None = None, max_reports: int = 40, top: int = 20
) -> dict[str, Any]:
    """Which names show up most across recent congressional disclosures.

    A count of disclosures, not of conviction or size: every trade counts once
    regardless of whether it was a $1,001 band or a $5,000,001 one.
    """
    result = congress_trades(year=year, max_reports=max_reports)

    tally: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"purchases": 0, "sales": 0, "members": set()}
    )
    for trade in result["trades"]:
        row = tally[trade["ticker"]]
        if trade["transaction"].startswith("P"):
            row["purchases"] += 1
        elif trade["transaction"].startswith("S"):
            row["sales"] += 1
        row["members"].add(trade["member"])

    ranked = sorted(
        (
            {
                "ticker": ticker,
                "purchases": row["purchases"],
                "sales": row["sales"],
                "net_disclosures": row["purchases"] - row["sales"],
                "distinct_members": len(row["members"]),
            }
            for ticker, row in tally.items()
        ),
        key=lambda r: (r["purchases"] + r["sales"]),
        reverse=True,
    )[:top]

    return {
        "year": result["year"],
        "ptr_filings_read": result["ptr_filings_read"],
        "ptr_filings_total": result["ptr_filings_total"],
        "most_traded": ranked,
        "limitations": result["limitations"]
        + [
            "Counts disclosures, not dollars. One member's $1M purchase and "
            "another's $1,001 purchase count the same."
        ],
    }


def recent_window(days: int = 90) -> str:
    """Cutoff date for callers that want to describe a lookback in prose."""
    return (datetime.now() - timedelta(days=days)).date().isoformat()
