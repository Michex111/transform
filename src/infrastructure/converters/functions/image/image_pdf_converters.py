"""Image -> PDF converters (Pillow, with cairosvg for SVG).

The raster sources are the ones the image converters already round-trip with
Pillow (jpg, jpeg, png, webp, gif, bmp, tiff, ico, avif); SVG takes a different
route and is rendered straight to a *vector* PDF by cairosvg.

Single-output contract
----------------------
A conversion job produces exactly one output file, and PDF is natively
multi-page, so no container (``.zip``) is ever needed here — unlike
``pdf -> png``, which has to bundle page images. A multi-frame source (animated
GIF, multi-page TIFF) simply becomes a multi-page PDF: page 1 is the first
frame, so nothing is silently dropped.

Page geometry
-------------
Every page keeps the pixel dimensions of its frame, scaled by the source's own
DPI when that is plausible, so a 300 DPI A4 scan comes out as an A4 PDF page
instead of a billboard. Without usable DPI metadata the scale is 72 DPI, i.e.
one pixel maps to one PDF point — the conventional image-to-PDF behaviour.

Frames are written one at a time through Pillow's PDF ``append`` mode, so a
long TIFF/GIF never holds more than a single decoded frame in memory (the same
reason ``pdf -> tiff`` streams rather than materialising its pages).

SVG is excluded from the raster list on purpose: rasterising it would throw
away the vector data cairosvg can put directly into the PDF.
"""

import logging
import math
from collections.abc import Iterator
from pathlib import Path

from PIL import Image, ImageSequence

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

#: Raster sources decoded with Pillow, matching the formats registered by
#: ``image_converters`` (HEIC is absent there because it needs pillow-heif).
RASTER_SOURCES = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "ico", "avif"]

#: Vector source rendered by cairosvg (no raster round-trip).
VECTOR_SOURCES = ["svg"]

#: Pixels-to-points scale used when the source carries no usable DPI metadata.
DEFAULT_RESOLUTION = 72.0

#: Pillow's PDF writer stores RGB/L/CMYK pages as JPEG (DCTDecode) streams, so
#: this is the quality of every colour page. Greyscale 1-bit scans and paletted
#: images are stored losslessly and ignore it.
JPEG_QUALITY = 85

#: A source DPI is only trusted inside these bounds. Some editors write 0/1
#: (page would be enormous) or absurd values like 30000 (page would be a
#: postage stamp), so anything outside the range falls back to the default.
MIN_TRUSTED_DPI = 20.0
MAX_TRUSTED_DPI = 1200.0


def _resolution(image: Image.Image) -> float:
    """Pixels-per-inch to lay the page out with, from the source's DPI.

    Returns ``DEFAULT_RESOLUTION`` when the metadata is missing, non-numeric,
    degenerate or outside the trusted range.
    """
    dpi = image.info.get("dpi")
    if not isinstance(dpi, (tuple, list)) or not dpi:
        return DEFAULT_RESOLUTION

    values = [float(value) for value in dpi[:2] if isinstance(value, (int, float))]
    if not values:
        return DEFAULT_RESOLUTION

    # One scale is used for every page (that is what Pillow's PDF writer
    # receives), so a mixed pair collapses to the smaller — never upscaling a
    # page past what its own metadata asked for.
    candidate = min(values)
    if not math.isfinite(candidate) or not MIN_TRUSTED_DPI <= candidate <= MAX_TRUSTED_DPI:
        return DEFAULT_RESOLUTION
    return candidate


def _flatten(image: Image.Image) -> Image.Image:
    """Return an opaque RGB copy of a frame.

    PDF pages have no transparent background, so anything translucent is
    composited onto white — otherwise a transparent PNG corners or an animated
    GIF frame would come out black (or as an unsupported mode).
    """
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if image.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        # 16/32-bit numeric modes have no PDF representation. Pillow's own
        # conversion to 8-bit grayscale rescales the range rather than
        # clipping it (65535 -> 255), which keeps 16-bit scans usable.
        return image.convert("L")
    return image.convert("RGB")


def _frames(image: Image.Image) -> Iterator[Image.Image]:
    """Yield each frame of ``image`` flattened to RGB, in order.

    ``ImageSequence.Iterator`` handles the frame compositing/disposal rules of
    animated GIFs, so frame *n* already includes whatever earlier frames left
    visible.
    """
    if getattr(image, "n_frames", 1) <= 1:
        yield _flatten(image)
        return
    for frame in ImageSequence.Iterator(image):
        # A detached copy is required: the iterator mutates the shared image
        # object on every step, which would corrupt a lazily-consumed page.
        yield _flatten(frame.copy())


def _convert_image_to_pdf(input_file: str, output_file: str) -> None:
    """Write the image (all frames) at ``input_file`` into a PDF."""
    path = Path(output_file)
    pages = 0
    size = (0, 0)
    try:
        with Image.open(input_file) as image:
            resolution = _resolution(image)
            frames = _frames(image)
            try:
                first = next(frames)
            except StopIteration:  # pragma: no cover - Pillow always yields one
                raise RuntimeError(f"{input_file} contains no image data to convert") from None

            first.save(path, "PDF", resolution=resolution, quality=JPEG_QUALITY)
            pages = 1
            size = first.size
            for frame in frames:
                # Append mode rewrites the cross-reference table and adds one
                # page, so only the current frame is resident in memory.
                with path.open("a+b") as handle:
                    frame.save(
                        handle, "PDF", append=True, resolution=resolution, quality=JPEG_QUALITY
                    )
                pages += 1
    except Exception as error:
        # Never leave a half-written PDF behind for the worker to upload as a
        # successful conversion.
        path.unlink(missing_ok=True)
        logger.debug("Image to PDF conversion failed for %s: %s", input_file, error)
        raise

    if not path.is_file() or path.stat().st_size == 0:  # pragma: no cover - defensive
        raise RuntimeError(f"PDF writer produced no output for {input_file}")

    logger.debug(
        "Wrote %s page(s) of %s into %s at %s DPI (first page %sx%s px)",
        pages,
        input_file,
        path.name,
        resolution,
        size[0],
        size[1],
    )


def _convert_svg_to_pdf(input_file: str, output_file: str) -> None:
    """Render an SVG straight to a vector PDF with cairosvg."""
    try:
        import cairosvg
    except ImportError:
        raise RuntimeError(
            "cairosvg is required for SVG to PDF conversion. Install with: pip install cairosvg"
        ) from None

    path = Path(output_file)
    try:
        cairosvg.svg2pdf(url=input_file, write_to=output_file)
    except Exception as error:
        path.unlink(missing_ok=True)
        logger.debug("SVG to PDF conversion failed for %s: %s", input_file, error)
        raise

    if not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"cairosvg produced no PDF output for {input_file}")
    logger.debug("Rendered %s to a vector PDF at %s", input_file, path.name)


def _make_image_converter(source: str):
    """Build a ``source -> pdf`` converter.

    SVG is dispatched to cairosvg; every other source goes through Pillow.
    """

    if source in VECTOR_SOURCES:

        def converter(input_file: str, output_file: str, logger_override=None) -> None:
            del logger_override
            _convert_svg_to_pdf(input_file, output_file)

    else:

        def converter(input_file: str, output_file: str, logger_override=None) -> None:
            del logger_override
            _convert_image_to_pdf(input_file, output_file)

    converter.__name__ = f"image_{source}_to_pdf"
    return converter


for _source in [*RASTER_SOURCES, *VECTOR_SOURCES]:
    registry.register(ConversionType(_source, "pdf"))(_make_image_converter(_source))
