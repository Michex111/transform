"""Image converters using Pillow and cairosvg.

Supports: JPEG ↔ PNG, PNG ↔ WEBP, SVG → PNG.
"""

import logging

from PIL import Image

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

# Conversion type definitions
jpeg_to_png = ConversionType("jpeg", "png")
png_to_jpeg = ConversionType("png", "jpeg")
jpg_to_png = ConversionType("jpg", "png")
png_to_jpg = ConversionType("png", "jpg")
png_to_webp = ConversionType("png", "webp")
webp_to_png = ConversionType("webp", "png")
svg_to_png = ConversionType("svg", "png")


def _convert_image(input_file: str, output_file: str, output_format: str) -> None:
    """Convert an image to a different format using Pillow."""
    with Image.open(input_file) as img:
        if img.mode in ("RGBA", "P") and output_format.upper() in ("JPEG", "JPG"):
            img = img.convert("RGB")
        img.save(output_file, format=output_format)


@registry.register(jpeg_to_png)
def jpeg_to_png_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "PNG")


@registry.register(png_to_jpeg)
def png_to_jpeg_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "JPEG")


@registry.register(jpg_to_png)
def jpg_to_png_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "PNG")


@registry.register(png_to_jpg)
def png_to_jpg_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "JPEG")


@registry.register(png_to_webp)
def png_to_webp_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "WEBP")


@registry.register(webp_to_png)
def webp_to_png_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _convert_image(input_file, output_file, "PNG")


@registry.register(svg_to_png)
def svg_to_png_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert SVG to PNG using cairosvg."""
    try:
        import cairosvg
        cairosvg.svg2png(url=input_file, write_to=output_file)
    except ImportError:
        raise RuntimeError(
            "cairosvg is required for SVG to PNG conversion. Install with: pip install cairosvg"
        )
