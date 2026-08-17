"""Ebook converters using calibre's ebook-convert CLI.

Supports: EPUB ↔ PDF, EPUB ↔ MOBI, EPUB ↔ TXT.
"""

import subprocess
import logging

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

# Conversion type definitions
epub_to_pdf = ConversionType("epub", "pdf")
pdf_to_epub = ConversionType("pdf", "epub")
epub_to_mobi = ConversionType("epub", "mobi")
mobi_to_epub = ConversionType("mobi", "epub")
epub_to_txt = ConversionType("epub", "txt")


def _run_ebook_convert(input_file: str, output_file: str, extra_args: list[str] | None = None) -> None:
    """Run calibre's ebook-convert tool."""
    cmd = ["ebook-convert", input_file, output_file]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ebook-convert failed: {result.stderr}")


@registry.register(epub_to_pdf)
def epub_to_pdf_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ebook_convert(input_file, output_file)


@registry.register(pdf_to_epub)
def pdf_to_epub_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ebook_convert(input_file, output_file, ["--enable-heuristics"])


@registry.register(epub_to_mobi)
def epub_to_mobi_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ebook_convert(input_file, output_file)


@registry.register(mobi_to_epub)
def mobi_to_epub_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ebook_convert(input_file, output_file)


@registry.register(epub_to_txt)
def epub_to_txt_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ebook_convert(input_file, output_file)
