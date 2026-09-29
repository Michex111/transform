"""Unit tests for the assistant's document text extractor.

Two properties matter more than any particular format's fidelity: extraction
never raises (a corrupt upload must not turn a chat turn into a 500), and it is
bounded (a huge file must be refused before it is read, and a long document
truncated rather than sent whole). Everything else here is the per-format
sanity check that the extraction found the *text* and not the markup.
"""

import io
import zipfile

from src.infrastructure.adapters.documents.text_extractor import DocumentTextExtractor
from tests.fixtures.documents import (
    docx_bytes,
    minimal_pdf,
    pptx_bytes,
    xlsx_bytes,
)


def extractor(
    *, max_document_bytes: int = 10 * 1024 * 1024, max_input_chars: int = 1000
) -> DocumentTextExtractor:
    return DocumentTextExtractor(
        max_document_bytes=max_document_bytes, max_input_chars=max_input_chars
    )


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def test_reads_a_plain_text_file() -> None:
    result = extractor().extract(file_name="notes.txt", data=b"Hello there.")
    assert result.supported is True
    assert result.text == "Hello there."
    assert result.truncated is False
    assert result.note == ""


def test_reads_utf8_with_a_bom() -> None:
    result = extractor().extract(file_name="notes.md", data=b"\xef\xbb\xbf# Title")
    assert result.text == "# Title"


def test_collapses_whitespace_and_blank_line_runs() -> None:
    data = b"Line   one\r\n\r\n\r\n  Line two  \r\n"
    result = extractor().extract(file_name="notes.txt", data=data)
    assert result.text == "Line one\n\nLine two"


def test_an_empty_file_is_readable_but_empty() -> None:
    result = extractor().extract(file_name="notes.txt", data=b"")
    assert result.supported is True
    assert result.text == ""


def test_truncates_a_long_document() -> None:
    result = extractor(max_input_chars=10).extract(file_name="notes.txt", data=b"x" * 100)
    assert result.text == "x" * 10
    assert result.truncated is True


def test_refuses_a_document_over_the_byte_budget() -> None:
    result = extractor(max_document_bytes=4).extract(file_name="notes.txt", data=b"12345")
    assert result.supported is False
    assert "larger than" in result.note
    assert result.text == ""


def test_unsupported_format_is_reported_not_raised() -> None:
    result = extractor().extract(file_name="archive.zip", data=b"PK\x03\x04")
    assert result.supported is False
    assert "readable formats" in result.note


def test_a_file_with_no_extension_is_reported() -> None:
    result = extractor().extract(file_name="README", data=b"text")
    assert result.supported is False


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_reads_a_pdf_text_layer() -> None:
    result = extractor().extract(
        file_name="report.pdf", data=minimal_pdf("Hello from the PDF")
    )
    assert result.supported is True
    assert "Hello from the PDF" in result.text


def test_a_corrupt_pdf_is_reported_not_raised() -> None:
    result = extractor().extract(file_name="broken.pdf", data=b"%PDF-1.4 not really")
    assert result.supported is False
    assert "could not be parsed" in result.note


# ---------------------------------------------------------------------------
# OOXML
# ---------------------------------------------------------------------------


def test_reads_a_docx() -> None:
    result = extractor().extract(file_name="report.docx", data=docx_bytes())
    assert result.supported is True
    assert "Quarterly report" in result.text
    assert "Revenue is up 12%." in result.text
    assert "w:document" not in result.text


def test_reads_a_pptx() -> None:
    result = extractor().extract(file_name="deck.pptx", data=pptx_bytes())
    assert result.supported is True
    assert "Welcome to the deck" in result.text
    assert "Second bullet" in result.text


def test_reads_an_xlsx_with_shared_strings() -> None:
    result = extractor().extract(file_name="sheet.xlsx", data=xlsx_bytes())
    assert result.supported is True
    # Cells are joined with a tab, which the whitespace pass then normalises.
    assert "Name Widget" in result.text
    assert "42" in result.text


def test_a_docx_missing_its_document_part_is_reported() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docProps/core.xml", b"<x/>")
    result = extractor().extract(file_name="broken.docx", data=buffer.getvalue())
    assert result.supported is False
    assert "could not be parsed" in result.note
def test_an_xml_entity_bomb_is_refused() -> None:
    """A DTD is never needed here, and expanding one is a cheap denial of service."""
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<w:document xmlns:w="http://x"><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:document>"""
    result = extractor().extract(file_name="evil.docx", data=docx_bytes(bomb))
    assert result.supported is False
def test_a_non_zip_docx_is_reported_not_raised() -> None:
    result = extractor().extract(file_name="report.docx", data=b"not a zip at all")
    assert result.supported is False
    assert "could not be parsed" in result.note


def test_a_decompression_bomb_member_is_refused_before_it_is_read() -> None:
    """Regression: a tiny ``.docx`` must not expand to an unbounded allocation.

    ``ZipFile.read`` materialises the *whole* member in memory, so a document
    whose ``word/document.xml`` declares a huge ``file_size`` was read in full
    before any text was needed — the exact "never read a gigabyte into memory"
    failure the extractor promises not to have. The compressed bytes here are
    tiny (well inside the byte budget) while the member expands far beyond the
    allowed ratio, which is the shape of a DEFLATE bomb.
    """
    budget = 512
    body = (
        b"<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>"
        + b"a" * 20_000
        + b"</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", body)
    data = buffer.getvalue()

    # The compressed document fits the caller's byte budget, so the pre-parse
    # size check cannot be what rejects it — the expansion guard must.
    assert len(data) <= budget

    result = extractor(max_document_bytes=budget).extract(
        file_name="bomb.docx", data=data
    )

    assert result.supported is False
    assert result.text == ""
