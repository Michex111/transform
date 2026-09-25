"""PDF <-> document converters.

**Every conversion whose source is a PDF lives here**, so a PDF has exactly one
owner. That matters because the registry is a plain dict and ``register``
overwrites: ``functions/__init__.py`` imports ``office`` *before* ``document``,
so a PDF-source pair registered in ``office_converters`` used to be silently
replaced by this module's version (or the other way round) — which is how
``pdf -> doc`` came to be served by a converter that could never work.

Two mechanisms, picked per target:

* **LibreOffice** (with ``--infilter=writer_pdf_import``) for the document
  formats. A PDF input *requires* that explicit import filter: without it
  LibreOffice has no filter that can turn a PDF into a Writer document, so it
  writes nothing at all **while still exiting 0**, which reached users as
  "LibreOffice reported success but produced no .doc output".
  Note what this import actually gives you: the pages come in as page
  images/frames, so the result looks like the PDF but is not editable text.
* **pypdfium2** for ``txt`` and ``md``. LibreOffice exports an *empty* file for
  an imported PDF — the text lives in the PDF page objects, not in the Writer
  document it built — so a text target extracts the real text layer instead,
  with the same library the image converters already depend on.

There is deliberately **no ``pdf -> html``**: LibreOffice's HTML export writes
its page images as separate ``*_html_*.gif`` files beside the markup, and this
pipeline uploads exactly one output object, so the HTML would ship with dangling
``<img src>`` references. Doing it properly needs either a zipped html+assets
output or a text-only rendering, and neither belongs under a plain
"PDF to HTML" promise.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

docx_to_pdf_conversion = ConversionType(source_format="docx", target_format="pdf")

#: LibreOffice's PDF import filter. Required for every PDF -> Writer conversion.
PDF_IMPORT_FILTER_ARG = "--infilter=writer_pdf_import"

#: Document targets LibreOffice can write from an imported PDF.
LIBREOFFICE_PDF_TARGETS = ("docx", "doc", "odt", "rtf")

#: Text targets served by extracting the PDF text layer directly.
TEXT_PDF_TARGETS = ("txt", "md")


def _move_output_to(out_dir: Path, src: Path, target_ext: str, output_file: Path) -> None:
    """Locate the file LibreOffice actually wrote and move it to ``output_file``.

    LibreOffice derives the output name from the *source* filename (e.g.
    ``plain_def.pdf`` -> ``plain_def.docx``), which does not match the expected
    ``output_file`` path when the worker decrypted the input into a
    ``plain_*`` sibling. We find the produced file (expected name first, then a
    permissive scan of the outdir) and rename it to the caller's target path so
    we never "succeed" with a missing/misnamed output.
    """
    candidates = [
        out_dir / f"{src.stem}.{target_ext}",
        out_dir / f"{src.name.split('.')[0]}.{target_ext}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            candidate.replace(output_file)
            return

    try:
        produced = [
            p
            for p in out_dir.iterdir()
            if p.is_file()
            and p.suffix.lstrip(".").lower() == target_ext.lower()
            and p != src
        ]
    except FileNotFoundError:
        produced = []

    if not produced:
        raise RuntimeError(
            f"LibreOffice reported success but produced no .{target_ext} output "
            f"for {src.name} (outdir={out_dir})."
        )

    produced[0].replace(output_file)


def extract_pdf_text(pdf_file: str) -> str:
    """Every extractable character in ``pdf_file``, page by page.

    Returns ``""`` for a PDF with no text layer (a scan, or a page of pure
    images) — the caller decides whether that is a failure. Kept separate from
    the conversion so it can be tested without one.
    """
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_file)
    try:
        pages: list[str] = []
        for page in document:
            textpage = page.get_textpage()
            try:
                pages.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
    return "\n".join(pages)


def _write_pdf_text(pdf_file: str, output_file: str) -> None:
    """Write a PDF's text layer to ``output_file``.

    Fails loudly on an empty extraction instead of writing a 0-byte file: the
    user asked for the document's text, and an empty result almost always means
    the PDF is a scan with no text layer, which is worth saying out loud.
    Returning a blank file would look like a successful conversion.
    """
    text = extract_pdf_text(pdf_file).strip()
    if not text:
        raise RuntimeError(
            f"No text could be extracted from {Path(pdf_file).name}. It may be a "
            "scanned or image-only PDF, which has no text layer to convert."
        )
    Path(output_file).write_text(text + "\n", encoding="utf-8")


def _convert_pdf_with_libreoffice(pdf_file: str, output_file: str, target_ext: str) -> None:
    """Convert a PDF to a Writer ``target_ext`` using LibreOffice headless."""
    pdf_path = Path(pdf_file).resolve()
    out_path = Path(output_file).resolve()
    out_dir = out_path.parent

    lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo_bin:
        raise RuntimeError("LibreOffice is not installed or not found in PATH")

    command = [
        lo_bin,
        "--headless",
        # Without this flag LibreOffice refuses to write anything for a PDF
        # input, and still exits 0 — see the module docstring.
        PDF_IMPORT_FILTER_ARG,
        "--convert-to", target_ext,
        "--outdir", out_dir,
        pdf_path,
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to convert PDF to {target_ext.upper()}: {e.stderr.strip()}"
        ) from e

    # A missing writer is reported as "Error: no export filter ..." on stdout
    # with exit code 0, so `check=True` cannot catch it and the produce-nothing
    # error below would blame the input. Surface the real reason instead.
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "no export filter" in output:
        raise RuntimeError(
            f"LibreOffice has no .{target_ext} writer, so this PDF cannot be "
            f"converted to .{target_ext}"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _move_output_to(out_dir, pdf_path, target_ext, out_path)


def _make_pdf_document_converter(target_ext: str):
    """Build the LibreOffice-backed converter for ``pdf -> target_ext``."""

    def converter(pdf_file: str, output_file: str, logger_override=None) -> None:
        log = logger_override or logger
        _convert_pdf_with_libreoffice(pdf_file, output_file, target_ext)
        log.debug(f"Converted {Path(pdf_file).name} -> {Path(output_file).name}")

    converter.__name__ = f"pdf_to_{target_ext}"
    return converter


def _make_pdf_text_converter(target_ext: str):
    """Build the text-extraction converter for ``pdf -> target_ext``.

    ``md`` receives the extracted text verbatim: plain text is already valid
    Markdown, so running it through another tool would only re-wrap the same
    characters.
    """

    def converter(pdf_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _write_pdf_text(pdf_file, output_file)

    converter.__name__ = f"pdf_to_{target_ext}"
    return converter


# Every ``pdf -> X`` conversion, registered from this one place.
for _target_ext in LIBREOFFICE_PDF_TARGETS:
    registry.register(ConversionType("pdf", _target_ext))(
        _make_pdf_document_converter(_target_ext)
    )
for _target_ext in TEXT_PDF_TARGETS:
    registry.register(ConversionType("pdf", _target_ext))(
        _make_pdf_text_converter(_target_ext)
    )


@registry.register(docx_to_pdf_conversion)
def docx_to_pdf(docx_file: str, pdf_file: str) -> None:
    """
    Convert a DOCX file to PDF format using LibreOffice headless.

    Args:
        docx_file (str): The path to the input DOCX file.
        pdf_file (str): The path to the output PDF file.

    Raises:
        RuntimeError: If the conversion process fails or produces no output.
    """
    docx_path = Path(docx_file).resolve()
    out_path = Path(pdf_file).resolve()
    out_dir = out_path.parent

    lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo_bin:
        raise RuntimeError("LibreOffice is not installed or not found in PATH")

    try:
        command = [
            lo_bin,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            docx_path,
        ]

        subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert DOCX to PDF: {e.stderr.strip()}") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _move_output_to(out_dir, docx_path, "pdf", out_path)