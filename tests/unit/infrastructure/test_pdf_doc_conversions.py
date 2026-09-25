"""Tests for the PDF-source conversions.

These pin the two things that were wrong and are easy to regress:

1. A PDF input needs LibreOffice's ``--infilter=writer_pdf_import``. Without it
   LibreOffice writes nothing *and exits 0*, which surfaced to users as
   "LibreOffice reported success but produced no .doc output" on a
   ``pdf -> doc`` conversion from the Files page.
2. A PDF has exactly **one** owner of its source conversions. The registry
   overwrites on re-registration, so a PDF-source pair registered in
   ``office_converters`` (imported first) silently replaces the one in
   ``pdf_docs_`` — which is how the broken ``pdf -> doc`` survived.

Everything here is a unit test with the external tools stubbed: no LibreOffice,
no pandoc and no pypdfium2 are required.
"""

import subprocess
import sys
import types
from pathlib import Path

import pytest

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import get_registry
from src.infrastructure.converters.functions.document import pdf_docs_
from src.infrastructure.converters.functions.office import office_converters


def _register_conversions() -> set[ConversionType]:
    """Every registered conversion, with the converter modules imported."""
    get_registry()
    return get_registry().list_conversions()


def _converter_name(source: str, target: str) -> str | None:
    converter = get_registry().get_converter(ConversionType(source, target))
    return getattr(converter, "__name__", None) if converter else None


class TestPdfHasOneOwner:
    """``pdf_docs_`` owns every ``pdf -> X`` conversion."""

    def test_office_converters_does_not_register_pdf_as_a_source(self):
        assert "pdf" not in office_converters.OFFICE_SOURCE_FORMATS, (
            "office_converters is imported before pdf_docs_, so a pdf source here "
            "would silently overwrite the working PDF converters"
        )
        assert "pdf" in office_converters.DOCUMENT_FORMATS, "pdf must stay a target"

    def test_every_advertised_pdf_document_conversion_is_owned_by_pdf_docs(self):
        """Nothing else may serve a `pdf -> document` edge.

        Image targets (`pdf -> png`, …) belong to `pdf_image_converters`; this
        pins the *document* family, which is where the duplicated registration
        lived.
        """
        document_targets = {"doc", "docx", "odt", "rtf", "txt", "md", "html", "tex", "wp"}
        for conversion in _register_conversions():
            if conversion.source_format != "pdf":
                continue
            if conversion.target_format not in document_targets:
                continue
            assert conversion.target_format in (
                *pdf_docs_.LIBREOFFICE_PDF_TARGETS,
                *pdf_docs_.TEXT_PDF_TARGETS,
            ), f"unexpected pdf document target: {conversion.target_format}"

    def test_expected_pdf_targets_are_registered(self):
        for target in ("doc", "docx", "odt", "rtf", "txt", "md"):
            assert _converter_name("pdf", target) == f"pdf_to_{target}"

    def test_document_targets_use_libreoffice_and_text_targets_do_not(self):
        for target in pdf_docs_.LIBREOFFICE_PDF_TARGETS:
            assert _converter_name("pdf", target) == f"pdf_to_{target}"
        for target in pdf_docs_.TEXT_PDF_TARGETS:
            assert _converter_name("pdf", target) == f"pdf_to_{target}"

    def test_pdf_to_html_is_not_advertised(self):
        # LibreOffice's HTML export writes page images as separate files beside
        # the markup and only one output object is uploaded, so the HTML would
        # ship with dangling <img src> references.
        assert _converter_name("pdf", "html") is None

    def test_docx_to_pdf_still_registered(self):
        assert _converter_name("docx", "pdf") == "docx_to_pdf"


class TestUnsupportedTargetsAreNotAdvertised:
    """Conversions that provably cannot succeed must not be offered."""

    def test_no_conversion_targets_wordperfect(self):
        # LibreOffice has no WordPerfect export filter (even for .wpd) and .wp is
        # not a registered extension, so every wp edge failed.
        targets = [c for c in _register_conversions() if c.target_format == "wp"]
        assert targets == [], f"wp must not be a target: {targets}"

    def test_wordperfect_is_not_a_source_either(self):
        sources = [c for c in _register_conversions() if c.source_format == "wp"]
        assert sources == [], f"wp must not be a source: {sources}"

    def test_latex_is_a_source_but_never_a_libreoffice_target(self):
        assert _converter_name("tex", "docx") is not None
        assert _converter_name("docx", "tex") is None
        # pandoc can write LaTeX from Markdown, so this edge stays.
        assert _converter_name("md", "tex") is not None
        assert _converter_name("tex", "md") is not None

    def test_markdown_and_pdf_edges_are_registered(self):
        assert _converter_name("md", "pdf") == "md_to_pdf"
        assert _converter_name("pdf", "md") == "pdf_to_md"


class TestPdfImportFilter:
    """The exact defect: a PDF input needs the Writer import filter."""

    @pytest.fixture
    def captured_command(self, monkeypatch, tmp_path):
        """Run the converter with LibreOffice stubbed, returning the argv used."""
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            produced = tmp_path / "input.docx"
            produced.write_bytes(b"docx")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(pdf_docs_.shutil, "which", lambda name: "/usr/bin/soffice")
        monkeypatch.setattr(pdf_docs_.subprocess, "run", fake_run)

        pdf = tmp_path / "input.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        out = tmp_path / "input.docx"
        pdf_docs_._convert_pdf_with_libreoffice(str(pdf), str(out), "docx")
        return calls[0]

    def test_the_import_filter_is_passed(self, captured_command):
        assert pdf_docs_.PDF_IMPORT_FILTER_ARG in captured_command

    def test_the_filter_is_a_single_argument_on_the_command_line(self, captured_command):
        # Passed as one argv entry, not as "--infilter writer_pdf_import".
        assert "--infilter=writer_pdf_import" in captured_command
        assert "--infilter" not in captured_command


class TestSilentLibreOfficeFailures:
    """LibreOffice exits 0 when it has no writer, so the output must be checked."""

    def test_missing_export_filter_is_reported_as_such(self, monkeypatch, tmp_path):
        stderr = (
            "Error: no export filter for /tmp/x/report.doc found, aborting.\n"
            "Error: no export filter"
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "", stderr)

        monkeypatch.setattr(pdf_docs_.shutil, "which", lambda name: "/usr/bin/soffice")
        monkeypatch.setattr(pdf_docs_.subprocess, "run", fake_run)

        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with pytest.raises(RuntimeError, match="no .doc writer"):
            pdf_docs_._convert_pdf_with_libreoffice(
                str(pdf), str(tmp_path / "report.doc"), "doc"
            )

    def test_office_converter_reports_a_missing_writer_accurately(
        self, monkeypatch, tmp_path
    ):
        """The misleading message the user saw must not come back."""

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0, "Error: no export filter for /tmp/x/a.tex found, aborting.", ""
            )

        monkeypatch.setattr(office_converters.shutil, "which", lambda name: "/usr/bin/soffice")
        monkeypatch.setattr(office_converters.subprocess, "run", fake_run)

        src = tmp_path / "notes.docx"
        src.write_bytes(b"docx")
        with pytest.raises(RuntimeError, match="no .tex writer"):
            office_converters._convert_with_libreoffice(str(src), str(tmp_path / "notes.tex"))


class TestPdfTextExtraction:
    """Text targets read the PDF's text layer instead of LibreOffice's empty export."""

    @pytest.fixture
    def fake_pdfium(self, monkeypatch):
        """A pypdfium2 double returning fixed text for two pages."""

        class FakeTextPage:
            def __init__(self, text: str):
                self._text = text
                self.closed = False

            def get_text_range(self) -> str:
                return self._text

            def close(self):
                self.closed = True

        class FakePage:
            def __init__(self, text: str):
                self._text = text
                self.closed = False

            def get_textpage(self) -> FakeTextPage:
                return FakeTextPage(self._text)

            def close(self):
                self.closed = True

        class FakeDocument:
            def __init__(self, _path, pages):
                self._pages = pages
                self.closed = False

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                self.closed = True

        module = types.ModuleType("pypdfium2")
        module.PdfDocument = lambda path: FakeDocument(path, [FakePage("page one"), FakePage("page two")])
        monkeypatch.setitem(sys.modules, "pypdfium2", module)
        return module

    def test_extracts_every_page(self, fake_pdfium, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        assert pdf_docs_.extract_pdf_text(str(pdf)) == "page one\npage two"

    def test_writes_the_extracted_text(self, fake_pdfium, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        out = tmp_path / "doc.txt"
        pdf_docs_._write_pdf_text(str(pdf), str(out))
        assert out.read_text(encoding="utf-8") == "page one\npage two\n"

    def test_an_image_only_pdf_fails_instead_of_writing_an_empty_file(
        self, monkeypatch, tmp_path
    ):
        """A 0-byte "successful" conversion is worse than an honest failure."""
        monkeypatch.setattr(pdf_docs_, "extract_pdf_text", lambda _path: "   \n  ")
        out = tmp_path / "scan.txt"
        with pytest.raises(RuntimeError, match="scanned or image-only PDF"):
            pdf_docs_._write_pdf_text("scan.pdf", str(out))
        assert not out.exists(), "no output file may be left behind"

    def test_the_registered_text_converter_uses_the_extractor(self, fake_pdfium, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        out = tmp_path / "doc.md"
        converter = get_registry().get_converter(ConversionType("pdf", "md"))
        assert converter is not None
        converter(str(pdf), str(out))
        assert "page one" in out.read_text(encoding="utf-8")
