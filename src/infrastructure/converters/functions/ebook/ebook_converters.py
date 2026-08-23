"""Ebook converters using calibre's ebook-convert CLI.

Supports pairwise conversion between the ebook formats in the UI catalog:
azw, azw3, epub, fb2, mobi, lit. Also allows pdf and txt as targets, which
calibre writes natively.
"""

import subprocess
import logging

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

EBOOK_FORMATS = ["azw", "azw3", "epub", "fb2", "mobi", "lit"]
# calibre can also read/write these.
EXTRA_FORMATS = ["pdf", "txt"]


def _run_ebook_convert(input_file: str, output_file: str, extra_args: list[str] | None = None) -> None:
    """Run calibre's ebook-convert tool."""
    cmd = ["ebook-convert", input_file, output_file]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ebook-convert failed: {result.stderr}")


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _run_ebook_convert(input_file, output_file)

    converter.__name__ = f"ebook_{source}_to_{target}"
    return converter


_all_formats = EBOOK_FORMATS + EXTRA_FORMATS
for _source in _all_formats:
    for _target in _all_formats:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )
