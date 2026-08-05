"""Reading the text of a filing, not just its metadata.

`get_filings` says an 8-K landed on a date. It cannot say what the 8-K said,
and that is usually the question -- a margin that moved, a quarter that beat
on revenue and sold off anyway, a restatement. Without this, an agent that
notices something odd in the numbers has to stop at "worth checking the 10-Q",
which is where most half-finished analysis ends.

Filings are HTML with inline XBRL tags wrapped around them. The parser here
strips markup to readable text and nothing more: no summarising, no section
inference beyond what the caller asks for. An agent reads the words the
company filed.

Documents are large -- a 10-K runs to hundreds of thousands of characters --
so text is returned in windows, either by offset or around a search term.
Truncation is always reported; a silently cut filing reads like a complete one.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from .client import EdgarError, fetch_text, resolve_cik
from .filings import recent_filings

# Markup that carries no readable content.
#
# The `ix:` entries matter more than they look. An inline-XBRL filing opens
# with a hidden header holding every context reference and element name in the
# document -- tens of thousands of characters of `us-gaap:RevenueMember`
# before a single readable word. Left in, it fills the first window, buries
# the actual filing, and matches search terms in a way that looks like a hit.
_SKIP_TAGS = {
    "script",
    "style",
    "head",
    "title",
    "ix:header",
    "ix:hidden",
    "ix:references",
    "ix:resources",
}

# Anything the filer hid from a human reader is hidden from us too.
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.IGNORECASE)

# Tags whose boundaries are real paragraph breaks in a filing's layout.
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6",
}

# Table cells need a separator but not a line break, or every figure in a
# financial table ends up on its own line -- and without one, adjacent cells
# fuse into a single meaningless number.
_CELL_TAGS = {"td", "th"}

DEFAULT_WINDOW = 20_000
MAX_WINDOW = 100_000


class _TextExtractor(HTMLParser):
    """Collect visible text, preserving block-level breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        # Names of the currently open tags that are hiding their contents, so
        # the right end tag lifts the suppression.
        self._hiding: list[str] = []

    def _hides(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _SKIP_TAGS:
            return True
        style = next((v for k, v in attrs if k == "style" and v), "")
        return bool(_HIDDEN_STYLE.search(style))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hides(tag, attrs):
            self._hiding.append(tag)
            return
        if self._hiding:
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        elif tag in _CELL_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._hiding:
            if self._hiding[-1] == tag:
                self._hiding.pop()
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        elif tag in _CELL_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hiding:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def to_text(html: str) -> str:
    """Filing markup reduced to readable text.

    Whitespace is collapsed but paragraph structure is kept, because tables of
    figures become unreadable when flattened onto one line.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        # Malformed markup: fall back to a blunt tag strip rather than failing
        # to return the filing at all. The hidden XBRL header still has to go,
        # or the fallback returns element names instead of a filing.
        stripped = re.sub(
            r"<ix:(header|hidden)\b.*?</ix:\1>", " ", html, flags=re.DOTALL | re.I
        )
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip()

    text = parser.text
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _locate(ticker: str, accession: str | None, form: str | None) -> dict[str, Any]:
    """The filing to read: a specific accession, or the newest of a form type."""
    if accession:
        wanted = accession.strip()
        normalised = wanted.replace("-", "")
        for filing in recent_filings(ticker, limit=1000):
            if filing["accession"].replace("-", "") == normalised:
                return filing
        raise EdgarError(
            f"accession {wanted} not found in the recent filing history for "
            f"{ticker.upper()}"
        )

    if form:
        matches = recent_filings(ticker, forms=[form], limit=1)
        if not matches:
            raise EdgarError(f"no {form.upper()} on file for {ticker.upper()}")
        return matches[0]

    raise EdgarError("supply either an accession or a form to read")


def _fetch(filing: dict[str, Any], ticker: str) -> str:
    cik = resolve_cik(ticker)
    key = f"document_{cik:010d}_{filing['accession'].replace('-', '')}"
    return to_text(fetch_text(filing["url"], key))


def _provenance(filing: dict[str, Any], ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker.strip().upper(),
        "form": filing["form"],
        "meaning": filing["meaning"],
        "filed": filing["filed"],
        "period": filing["period"],
        "accession": filing["accession"],
        "url": filing["url"],
    }


def document_text(
    ticker: str,
    accession: str | None = None,
    form: str | None = None,
    offset: int = 0,
    max_chars: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """A window of a filing's text, with the provenance of the filing itself."""
    if offset < 0:
        raise EdgarError("offset cannot be negative")
    max_chars = max(1, min(int(max_chars), MAX_WINDOW))

    filing = _locate(ticker, accession, form)
    text = _fetch(filing, ticker)
    window = text[offset : offset + max_chars]
    end = offset + len(window)

    return {
        **_provenance(filing, ticker),
        "total_chars": len(text),
        "offset": offset,
        "returned_chars": len(window),
        "truncated": end < len(text),
        "next_offset": end if end < len(text) else None,
        "text": window,
        "note": (
            "Text as filed, markup stripped. Nothing here is summarised or "
            "paraphrased."
        ),
    }


def search_filing(
    ticker: str,
    query: str,
    accession: str | None = None,
    form: str | None = None,
    context: int = 800,
    max_hits: int = 5,
) -> dict[str, Any]:
    """Passages of a filing around every occurrence of a term.

    Faster and far cheaper than paging a 10-K to find the one paragraph about
    a tax benefit. Returns the surrounding text verbatim so the agent reads
    the company's own words rather than a match count.
    """
    if not query.strip():
        raise EdgarError("a search term is required")
    context = max(50, min(int(context), 5_000))

    filing = _locate(ticker, accession, form)
    text = _fetch(filing, ticker)

    hits: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(query.strip()), text, flags=re.IGNORECASE):
        start = max(0, match.start() - context)
        end = min(len(text), match.end() + context)
        hits.append(
            {
                "position": match.start(),
                "passage": text[start:end],
            }
        )
        if len(hits) >= max_hits:
            break

    return {
        **_provenance(filing, ticker),
        "query": query,
        "total_chars": len(text),
        "hits": hits,
        "hit_count": len(hits),
        "more_hits_possible": len(hits) >= max_hits,
        "note": (
            "No match means the term does not appear in this document — not "
            "that the fact is absent from the company's filings. Check the "
            "form type before concluding anything."
            if not hits
            else "Passages are verbatim, with surrounding context."
        ),
    }
