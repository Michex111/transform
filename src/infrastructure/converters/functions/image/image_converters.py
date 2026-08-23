"""Image converters using Pillow and cairosvg.

Supports pairwise conversion between the raster image formats in the UI
catalog: jpg, jpeg, png, webp, gif, bmp, tiff, ico, avif. SVG is converted via
cairosvg (rasterised to PNG).

HEIC is not registered here because it requires the ``pillow-heif`` plugin,
which is not part of the deployment.
"""

import logging

from PIL import Image

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

# Pillow format names keyed by the catalog extension.
FORMAT_BY_EXT = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
    "ico": "ICO",
    "avif": "AVIF",
}

RASTER_FORMATS = list(FORMAT_BY_EXT.keys())


def _convert_image(input_file: str, output_file: str, output_format: str) -> None:
    """Convert an image to a different format using Pillow."""
    with Image.open(input_file) as img:
        if img.mode in ("RGBA", "P") and output_format in ("JPEG", "JPG"):
            img = img.convert("RGB")
        img.save(output_file, format=output_format)


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _convert_image(input_file, output_file, FORMAT_BY_EXT[target])

    converter.__name__ = f"image_{source}_to_{target}"
    return converter


def svg_to_png_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert SVG to PNG using cairosvg."""
    del logger_override
    try:
        import cairosvg

        cairosvg.svg2png(url=input_file, write_to=output_file)
    except ImportError:
        raise RuntimeError(
            "cairosvg is required for SVG to PNG conversion. Install with: pip install cairosvg"
        ) from None


def _make_svg_converter(target: str):
    """Build an SVG -> raster converter that rasterises to PNG then re-encodes."""

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        import io

        import cairosvg

        png_bytes = cairosvg.svg2png(url=input_file)
        if png_bytes is None:
            raise RuntimeError(f"cairosvg returned no data for {input_file}")
        with Image.open(io.BytesIO(png_bytes)) as img:
            if target in ("jpg", "jpeg"):
                img = img.convert("RGB")
            img.save(output_file, format=FORMAT_BY_EXT[target])

    converter.__name__ = f"image_svg_to_{target}"
    return converter


# Register all pairwise raster conversions.
for _source in RASTER_FORMATS:
    for _target in RASTER_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )

# SVG -> raster conversions: PNG directly via cairosvg, others via rasterise+re-encode.
registry.register(ConversionType("svg", "png"))(svg_to_png_converter)
for _target in ["jpg", "jpeg", "webp", "gif", "bmp", "tiff", "ico", "avif"]:
    registry.register(ConversionType("svg", _target))(_make_svg_converter(_target))
