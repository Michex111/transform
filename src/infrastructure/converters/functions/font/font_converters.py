"""Font converters using fontTools.

Supports conversion between the font formats in the UI catalog:
ttf, otf, woff, woff2, eot.

``eot`` is read-only (fontTools can parse EOT but cannot write it), so it is
only registered as a source format.

Requires ``fontTools`` (with the ``woff`` extra for WOFF/WOFF2 support).
"""

import logging

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

# fontTools can write these formats.
WRITABLE_FORMATS = ["ttf", "otf", "woff", "woff2"]
# Read-only source (fontTools can parse it, but not emit it).
READABLE_FORMATS = WRITABLE_FORMATS + ["eot"]

# fontTools "flavor" for each output format. None = plain OpenType (ttf/otf).
_FLAVOR = {
    "ttf": None,
    "otf": None,
    "woff": "woff",
    "woff2": "woff2",
}


def _convert_font(input_file: str, output_file: str, target: str) -> None:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        raise RuntimeError(
            "fontTools is required for font conversion. Install with: pip install fontTools[woff]"
        ) from None

    font = TTFont(input_file)
    font.flavor = _FLAVOR[target]
    font.save(output_file)


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _convert_font(input_file, output_file, target)

    converter.__name__ = f"font_{source}_to_{target}"
    return converter


for _source in READABLE_FORMATS:
    for _target in WRITABLE_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )
