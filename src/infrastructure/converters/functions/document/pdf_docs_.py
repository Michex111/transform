import logging
import shutil
import subprocess
from pathlib import Path

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

pdf_to_docx_conversion = ConversionType(source_format="pdf", target_format="docx")
docx_to_pdf_conversion = ConversionType(source_format="docx", target_format="pdf")


@registry.register(pdf_to_docx_conversion)
def pdf_to_docx(pdf_file: str, docx_file: str, logger_overide: logging.Logger | None = None) -> None:
    """
    Convert a PDF file to DOCX format using the 'pandoc' command-line tool.

    Args:
        pdf_file (str): The path to the input PDF file.
        docx_file (str): The path to the output DOCX file.

    Raises:
        subprocess.CalledProcessError: If the conversion process fails.
    """
    log = logger_overide or logger
    pdf_path = Path(pdf_file).resolve()
    out_dir = Path(docx_file).parent.resolve()

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

        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert PDF to DOCX: {e.stderr.strip()}") from e
    

@registry.register(docx_to_pdf_conversion)
def docx_to_pdf(docx_file: str, pdf_file: str) -> None:
    """
    Convert a DOCX file to PDF format using the 'pandoc' command-line tool.

    Args:
        docx_file (str): The path to the input DOCX file.
        pdf_file (str): The path to the output PDF file.

    Raises:
        subprocess.CalledProcessError: If the conversion process fails.
    """

    docx_path = Path(docx_file).resolve()
    out_dir = Path(pdf_file).parent.resolve()

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