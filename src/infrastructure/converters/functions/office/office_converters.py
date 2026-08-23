"""Office converters (documents, spreadsheets, presentations).

Documents, spreadsheets and presentations are converted with LibreOffice
headless. Markdown (``md``) is handled by pandoc because LibreOffice cannot
read/write Markdown directly.

Supported formats:
    - Documents:      doc, docx, odt, rtf, txt, pdf, tex, wp, html, md
    - Spreadsheets:   xls, xlsx, ods, csv
    - Presentations:  ppt, pptx, odp

Apple iWork formats (``pages``, ``numbers``, ``key``) are listed in the UI
catalog but are not writable by LibreOffice, so they are intentionally not
registered here.
"""

import logging
import shutil
import subprocess
from pathlib import Path

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

DOCUMENT_FORMATS = ["doc", "docx", "odt", "rtf", "txt", "pdf", "tex", "wp", "html"]
SPREADSHEET_FORMATS = ["xls", "xlsx", "ods", "csv"]
PRESENTATION_FORMATS = ["ppt", "pptx", "odp", "pdf"]
MARKDOWN_FORMATS = ["md", "txt", "docx", "odt", "rtf", "tex", "html", "pdf"]


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


def _register_all(formats: list[str], builder) -> None:
    """Register every distinct source -> target pair for a list of formats."""
    for source in formats:
        for target in formats:
            if source == target:
                continue
            registry.register(ConversionType(source, target))(builder(source, target))


# Register pairwise conversions within each office family.
_register_all(DOCUMENT_FORMATS, _make_office_converter)
_register_all(SPREADSHEET_FORMATS, _make_office_converter)
_register_all(PRESENTATION_FORMATS, _make_office_converter)

# Markdown is handled by pandoc. Register md <-> other writable formats.
for fmt in MARKDOWN_FORMATS:
    if fmt == "md":
        continue
    registry.register(ConversionType("md", fmt))(_make_markdown_converter("md", fmt))
    registry.register(ConversionType(fmt, "md"))(_make_markdown_converter(fmt, "md"))
