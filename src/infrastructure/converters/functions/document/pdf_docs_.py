import logging
import shutil
import subprocess
from pathlib import Path

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

pdf_to_docx_conversion = ConversionType(source_format="pdf", target_format="docx")
docx_to_pdf_conversion = ConversionType(source_format="docx", target_format="pdf")


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


@registry.register(pdf_to_docx_conversion)
def pdf_to_docx(pdf_file: str, docx_file: str, logger_overide: logging.Logger | None = None) -> None:
    """
    Convert a PDF file to DOCX format using LibreOffice headless.

    Args:
        pdf_file (str): The path to the input PDF file.
        docx_file (str): The path to the output DOCX file.

    Raises:
        RuntimeError: If the conversion process fails or produces no output.
    """
    log = logger_overide or logger
    pdf_path = Path(pdf_file).resolve()
    out_path = Path(docx_file).resolve()
    out_dir = out_path.parent

    lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo_bin:
        raise RuntimeError("LibreOffice is not installed or not found in PATH")

    try:
        command = [
            lo_bin,
            "--headless",
            "--infilter=writer_pdf_import",
            "--convert-to", "docx",
            "--outdir", out_dir,
            pdf_path,
        ]

        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert PDF to DOCX: {e.stderr.strip()}") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _move_output_to(out_dir, pdf_path, "docx", out_path)
    log.debug(f"Converted {pdf_path.name} -> {out_path.name}")


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

        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert DOCX to PDF: {e.stderr.strip()}") from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _move_output_to(out_dir, docx_path, "pdf", out_path)