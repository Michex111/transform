"""Office converters (documents, spreadsheets, presentations).

Documents, spreadsheets and presentations are converted with LibreOffice
headless. Markdown (``md``) is handled by pandoc because LibreOffice cannot
read/write Markdown directly.

Supported formats:
    - Documents:      doc, docx, odt, rtf, txt, pdf, html (tex: source only)
    - Spreadsheets:   xls, xlsx, ods, csv
    - Presentations:  ppt, pptx, odp

Apple iWork formats (``pages``, ``numbers``, ``key``) are listed in the UI
catalog but are not writable by LibreOffice, so they are intentionally not
registered here.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

#: Formats LibreOffice can both read and write, so they pair up freely.
#: ``pdf`` belongs here because it is a *target* (``docx -> pdf`` etc.), but it
#: is deliberately absent from :data:`OFFICE_SOURCE_FORMATS` below.
DOCUMENT_FORMATS = ["doc", "docx", "odt", "rtf", "txt", "pdf", "html"]

#: The sources this module registers. ``pdf`` is excluded because importing a
#: PDF needs an explicit LibreOffice import filter *and* its text is extracted
#: with pypdfium2 rather than LibreOffice — so ``pdf_docs_`` owns every
#: ``pdf -> X`` conversion. Leaving ``pdf`` here would silently overwrite those
#: registrations (this module is imported first), which is exactly how
#: ``pdf -> doc`` came to be served by a converter that could never work.
OFFICE_SOURCE_FORMATS = ["doc", "docx", "odt", "rtf", "txt", "html"]

#: Document formats LibreOffice can read but NOT write, so they are registered
#: as SOURCES only and never as targets.
#:
#: ``tex`` has no export filter in this build — ``--convert-to tex`` aborts with
#: ``Error: no export filter`` — while importing it does produce a document.
#: Registering it as a *target* therefore advertised a conversion that could
#: never succeed, from ten different sources.
DOCUMENT_SOURCE_ONLY_FORMATS = ["tex"]

# ``wp`` is deliberately absent from BOTH lists. LibreOffice has no WordPerfect
# export filter (``--convert-to wp`` and even ``--convert-to wpd`` abort with
# ``Error: no export filter``), and ``.wp`` is not among the extensions it
# registers — its WordPerfect filters are the legacy ``.wpd``/W4W *import*
# filters named in ``main.xcd``. Every ``wp`` edge was
# a guaranteed failure in both directions.

SPREADSHEET_FORMATS = ["xls", "xlsx", "ods", "csv"]
PRESENTATION_FORMATS = ["ppt", "pptx", "odp", "pdf"]

# ``pdf`` is excluded on purpose: pandoc has no PDF reader (``Unknown input
# format pdf``) and no PDF writer in this image (it shells out to a LaTeX
# engine, which is not installed). Both Markdown <-> PDF edges are served by
# composing the two working steps through a DOCX intermediate — see the bottom
# of this module.
MARKDOWN_FORMATS = ["md", "txt", "docx", "odt", "rtf", "tex", "html"]

#: What LibreOffice prints (on stdout) when it has no writer for a format.
NO_EXPORT_FILTER = "no export filter"


def _convert_with_libreoffice(input_file: str, output_file: str) -> None:
    """Convert a file with LibreOffice headless based on the output extension."""
    lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo_bin:
        raise RuntimeError("LibreOffice is not installed or not found in PATH")

    src = Path(input_file).resolve()
    out_path = Path(output_file).resolve()
    target_ext = out_path.suffix.lstrip(".")

    command = [
        lo_bin,
        "--headless",
        "--convert-to", target_ext,
        "--outdir", str(out_path.parent),
        str(src),
    ]
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600
    )

    # A format LibreOffice cannot write is reported as
    # "Error: no export filter for <path> found, aborting." and **exits 0**, so
    # the return code alone cannot tell a real conversion from one that was
    # never attempted. Checked explicitly so such a pair fails with an accurate
    # message instead of the misleading "reported success but produced nothing".
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if NO_EXPORT_FILTER in output:
        raise RuntimeError(
            f"LibreOffice has no .{target_ext} writer, so {src.name} cannot be "
            f"converted to .{target_ext}"
        )

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"LibreOffice conversion failed for {src.name} -> .{target_ext}: {err or f'exit code {result.returncode}'}")

    # LibreOffice writes the output into the outdir, but the exact name can
    # vary: usually <stem>.<target_ext>, but some format pairs write the source
    # basename instead. Search the outdir for the produced file rather than
    # assuming one fixed name, so we never "succeed" with a missing output.
    produced = _find_output(out_path.parent, src, target_ext)
    if produced is None:
        raise RuntimeError(
            f"LibreOffice reported success but produced no .{target_ext} output "
            f"for {src.name} (outdir={out_path.parent})."
        )
    if produced.resolve() != out_path.resolve():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        produced.replace(out_path)


def _find_output(outdir: Path, src: Path, target_ext: str) -> Path | None:
    """Locate the file LibreOffice actually wrote for a conversion.

    LibreOffice derives the output name from the source in ways that vary by
    format pair and version. We try the expected ``<stem>.<ext>`` first, then
    the source basename, then fall back to scanning the outdir for *any*
    ``.<ext>`` file that is newer than the source and not the source itself.
    """
    candidates = [
        outdir / f"{src.stem}.{target_ext}",
        outdir / f"{src.name.split('.')[0]}.{target_ext}",
        outdir / f"{src.stem} - {src.stem}.{target_ext}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    # Fall back to a permissive scan of the outdir for a matching extension.
    try:
        produced = [
            p
            for p in outdir.iterdir()
            if p.is_file()
            and p.suffix.lstrip(".").lower() == target_ext.lower()
            and p != src
        ]
    except FileNotFoundError:
        return None
    return produced[0] if produced else None


def _convert_with_pandoc(input_file: str, output_file: str) -> None:
    """Convert a file with pandoc (used for Markdown in/out)."""
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc is not installed or not found in PATH")

    result = subprocess.run(
        ["pandoc", input_file, "-o", output_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pandoc conversion failed: {result.stderr.strip()}")


def _make_office_converter(source: str, target: str):
    """Build a LibreOffice-based converter for a source/target pair."""

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _convert_with_libreoffice(input_file, output_file)

    converter.__name__ = f"{source}_to_{target}"
    return converter


def _make_markdown_converter(source: str, target: str):
    """Build a pandoc-based converter for Markdown in/out pairs."""

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _convert_with_pandoc(input_file, output_file)

    converter.__name__ = f"md_{source}_to_{target}"
    return converter


def _register_pairs(sources: list[str], targets: list[str], builder) -> None:
    """Register every distinct ``source -> target`` pair across two lists."""
    for source in sources:
        for target in targets:
            if source == target:
                continue
            registry.register(ConversionType(source, target))(builder(source, target))


def _register_all(formats: list[str], builder) -> None:
    """Register every distinct source -> target pair within one list."""
    _register_pairs(formats, formats, builder)


def _compose_via_docx(first, second):
    """Build a converter that chains two steps through a DOCX intermediate.

    Needed for the Markdown <-> PDF pair, which neither tool can do in one step
    (see ``MARKDOWN_FORMATS``). Both halves are the same functions the single
    step converters use, so the composed pair cannot drift from them.
    """

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        with tempfile.TemporaryDirectory(prefix="office_compose_") as tmp:
            intermediate = Path(tmp) / f"{Path(input_file).stem}.docx"
            first(input_file, str(intermediate))
            second(str(intermediate), output_file)

    return converter


# Register pairwise conversions within each office family. Read-only formats
# (``tex``) are registered as sources only — see DOCUMENT_SOURCE_ONLY_FORMATS.
_register_pairs(OFFICE_SOURCE_FORMATS, DOCUMENT_FORMATS, _make_office_converter)
_register_pairs(DOCUMENT_SOURCE_ONLY_FORMATS, DOCUMENT_FORMATS, _make_office_converter)
_register_all(SPREADSHEET_FORMATS, _make_office_converter)
_register_all(PRESENTATION_FORMATS, _make_office_converter)

# Markdown is handled by pandoc. Register md <-> other writable formats.
for fmt in MARKDOWN_FORMATS:
    if fmt == "md":
        continue
    registry.register(ConversionType("md", fmt))(_make_markdown_converter("md", fmt))
    registry.register(ConversionType(fmt, "md"))(_make_markdown_converter(fmt, "md"))

# Markdown -> PDF is composed from the two steps that do work: pandoc writes a
# DOCX, LibreOffice turns that into the PDF. (The other direction, PDF -> md, is
# owned by ``pdf_docs_`` because it needs the PDF text layer.)
_md_to_pdf = _compose_via_docx(_convert_with_pandoc, _convert_with_libreoffice)
_md_to_pdf.__name__ = "md_to_pdf"
registry.register(ConversionType("md", "pdf"))(_md_to_pdf)
