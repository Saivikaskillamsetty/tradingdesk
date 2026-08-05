"""Filing text extraction, tested on synthetic markup.

These run offline. Filings are inline-XBRL HTML with tables of figures in
them; text that silently loses a table row, or that reports a complete
document when it returned the first slice, produces analysis that is confident
and wrong.
"""

from __future__ import annotations

import pytest

from desk_mcp.edgar import documents


class TestTextExtraction:
    def test_strips_tags_and_keeps_words(self):
        text = documents.to_text("<p>Revenue increased <b>22%</b>.</p>")

        assert "Revenue increased 22%." in text

    def test_drops_script_and_style_content(self):
        html = "<style>p{color:red}</style><script>var x=1</script><p>Real text</p>"
        text = documents.to_text(html)

        assert "Real text" in text
        assert "color" not in text
        assert "var x" not in text

    def test_table_rows_become_separate_lines(self):
        """Flattening a table of figures onto one line makes it unreadable."""
        text = documents.to_text("<tr><td>2025</td></tr><tr><td>2024</td></tr>")

        assert "2025" in text.split("\n")
        assert "2024" in text.split("\n")

    def test_adjacent_cells_do_not_fuse_into_one_number(self):
        """"45,183" beside "39,001" must not become "45,18339,001"."""
        text = documents.to_text("<tr><td>45,183</td><td>39,001</td></tr>")

        assert "45,183 39,001" in text

    def test_entities_are_decoded(self):
        assert "R&D" in documents.to_text("<p>R&amp;D expense</p>")

    def test_non_breaking_space_is_normalised(self):
        text = documents.to_text("<p>net&nbsp;income</p>")

        assert "net income" in text

    def test_inline_xbrl_tags_are_stripped_but_their_figures_kept(self):
        html = '<ix:nonFraction contextRef="c1">45,183</ix:nonFraction>'

        assert "45,183" in documents.to_text(html)

    def test_hidden_xbrl_header_is_dropped(self):
        """It runs to tens of thousands of characters and buries the filing."""
        html = (
            "<ix:header><ix:hidden>"
            "us-gaap:RevenueMember us-gaap:CommonStockMember"
            "</ix:hidden></ix:header><p>Form 6-K</p>"
        )
        text = documents.to_text(html)

        assert text == "Form 6-K"

    def test_content_the_filer_hid_stays_hidden(self):
        html = '<div style="display:none">context refs</div><p>Total revenue</p>'
        text = documents.to_text(html)

        assert "context refs" not in text
        assert "Total revenue" in text

    def test_hiding_ends_at_the_matching_close_tag(self):
        html = '<div style="display: none">hidden</div><div>visible</div>'
        text = documents.to_text(html)

        assert "hidden" not in text
        assert "visible" in text

    def test_runs_of_blank_lines_collapse(self):
        text = documents.to_text("<div>a</div><div></div><div></div><div>b</div>")

        assert "\n\n\n" not in text

    def test_malformed_markup_still_returns_text(self):
        """A broken filing must degrade, not fail — the words still matter."""
        text = documents.to_text("<p>unclosed <b>bold text")

        assert "unclosed" in text and "bold text" in text

    def test_plain_text_filing_passes_through(self):
        assert "ITEM 7." in documents.to_text("ITEM 7. MANAGEMENT'S DISCUSSION")


class TestWindowing:
    """Truncation must be visible; a cut filing reads like a complete one."""

    def test_window_reports_more_to_come(self, monkeypatch):
        self._stub(monkeypatch, "A" * 5_000)
        result = documents.document_text("TEST", accession="x", max_chars=1_000)

        assert result["returned_chars"] == 1_000
        assert result["truncated"] is True
        assert result["next_offset"] == 1_000
        assert result["total_chars"] == 5_000

    def test_final_window_reports_no_more(self, monkeypatch):
        self._stub(monkeypatch, "A" * 500)
        result = documents.document_text("TEST", accession="x", max_chars=1_000)

        assert result["truncated"] is False
        assert result["next_offset"] is None

    def test_offset_pages_forward(self, monkeypatch):
        self._stub(monkeypatch, "0123456789")
        result = documents.document_text(
            "TEST", accession="x", offset=4, max_chars=3
        )

        assert result["text"] == "456"

    def test_window_is_capped(self, monkeypatch):
        self._stub(monkeypatch, "A" * 200_000)
        result = documents.document_text(
            "TEST", accession="x", max_chars=500_000
        )

        assert result["returned_chars"] == documents.MAX_WINDOW

    def test_negative_offset_is_refused(self, monkeypatch):
        self._stub(monkeypatch, "text")
        with pytest.raises(documents.EdgarError, match="offset"):
            documents.document_text("TEST", accession="x", offset=-1)

    def test_provenance_travels_with_the_text(self, monkeypatch):
        self._stub(monkeypatch, "text")
        result = documents.document_text("test", accession="x")

        assert result["ticker"] == "TEST"
        assert result["form"] == "10-Q"
        assert result["accession"] == "0000000000-26-000001"
        assert result["filed"] == "2026-07-29"

    @staticmethod
    def _stub(monkeypatch, text: str) -> None:
        filing = {
            "form": "10-Q",
            "meaning": "quarterly report",
            "filed": "2026-07-29",
            "period": "2026-06-30",
            "accession": "0000000000-26-000001",
            "primary_document": "doc.htm",
            "url": "https://example.invalid/doc.htm",
        }
        monkeypatch.setattr(documents, "_locate", lambda *a, **k: filing)
        monkeypatch.setattr(documents, "_fetch", lambda *a, **k: text)


class TestSearch:
    def test_returns_surrounding_context(self, monkeypatch):
        TestWindowing._stub(
            monkeypatch, "x" * 100 + "stock-based compensation" + "y" * 100
        )
        result = documents.search_filing("TEST", query="compensation", context=50)

        assert result["hit_count"] == 1
        assert "stock-based compensation" in result["hits"][0]["passage"]

    def test_matching_is_case_insensitive(self, monkeypatch):
        TestWindowing._stub(monkeypatch, "Goodwill Impairment charge")
        result = documents.search_filing("TEST", query="goodwill impairment")

        assert result["hit_count"] == 1

    def test_hit_count_is_capped(self, monkeypatch):
        TestWindowing._stub(monkeypatch, "revenue " * 50)
        result = documents.search_filing("TEST", query="revenue", max_hits=3)

        assert result["hit_count"] == 3
        assert result["more_hits_possible"] is True

    def test_no_match_says_what_that_does_and_does_not_mean(self, monkeypatch):
        TestWindowing._stub(monkeypatch, "nothing relevant here")
        result = documents.search_filing("TEST", query="impairment")

        assert result["hits"] == []
        assert "not that the fact is absent" in result["note"]

    def test_empty_query_is_refused(self, monkeypatch):
        TestWindowing._stub(monkeypatch, "text")
        with pytest.raises(documents.EdgarError, match="search term"):
            documents.search_filing("TEST", query="  ")


class TestLocating:
    def test_neither_accession_nor_form_is_refused(self):
        with pytest.raises(documents.EdgarError, match="accession or a form"):
            documents._locate("AAPL", None, None)
