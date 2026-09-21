"""PDF -> raster image converters (pypdfium2 + Pillow).

Pages are rasterised with PDFium, which ships inside the ``pypdfium2`` wheel,
so the worker image needs no system package (no poppler-utils/ghostscript) for
these conversions. Encoding is done with Pillow, reusing the same format map
as the raster image converters.

Single-output contract
----------------------
A conversion job produces exactly one output file, so multi-page documents
need a container:

* ``pdf -> tiff`` and ``pdf -> gif`` are multi-frame formats: every page is
  written into the one file (TIFF pages / GIF frames), newest page last.
* ``pdf -> png|jpg|jpeg|webp|bmp`` writes a plain image when the PDF has a
  single page, and a ``.zip`` holding one image per page otherwise. The worker
  learns about the ``.zip`` extension from the converter's ``output_extension``
  hook (see ``converter_registry.converter_output_extension``), so the stored
  object and the download are named ``*.zip`` instead of mislabelling an
  archive as an image.
"""

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

#: Rasterisation resolution. 200 DPI keeps text crisp without producing
#: unreasonably large page images (an A4 page lands at ~1654x2339 px).
DEFAULT_DPI = 200

#: Upper bound for the longest edge of a rendered page, in pixels. Large-format
#: pages (posters, blueprints) would otherwise allocate gigabytes at
#: ``DEFAULT_DPI`` and could OOM-kill the worker, taking every queued job with
#: it. Pages above this are scaled down proportionally.
MAX_RENDER_SIDE = 6000

#: Pillow format name and encoder settings per catalog extension.
FORMAT_BY_EXT = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "gif": "GIF",
}

JPEG_QUALITY = 85
WEBP_QUALITY = 85

#: Targets that carry every page inside a single file.
MULTI_FRAME_TARGETS = frozenset({"tiff", "gif"})

#: Targets that get a one-image-per-page ``.zip`` when a PDF has several pages.
ARCHIVE_TARGETS = frozenset({"png", "jpg", "jpeg", "webp", "bmp"})

#: Inter-frame delay for multi-page GIFs (ms): the pages play as a slideshow.
GIF_PAGE_DURATION_MS = 1000


def _page_count(pdf_path: str | Path) -> int:
    """Number of pages in ``pdf_path`` (0 for a document with none)."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(document)
    finally:
        document.close()


def _scale_for_page(page: pdfium.PdfPage, dpi: int) -> float:
    """Render scale for ``page`` at ``dpi``, capped by ``MAX_RENDER_SIDE``."""
    scale = dpi / 72
    width, height = page.get_size()
    longest_side = max(width, height) * scale
    if longest_side > MAX_RENDER_SIDE:
        scale *= MAX_RENDER_SIDE / longest_side
    return scale


def _flatten(image: Image.Image) -> Image.Image:
    """Return an opaque RGB copy of a rendered page.

    PDF pages render with the page's own background; anything translucent is
    composited onto white so JPEG/BMP/GIF encoding never turns it black.
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
    return image.convert("RGB")


def _render_pages(pdf_path: str | Path, dpi: int = DEFAULT_DPI) -> Iterator[Image.Image]:
    """Yield each page of ``pdf_path`` as a flattened RGB image, in order."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(len(document)):
            page = document[index]
            # pypdfium2's stub types ``scale`` as int, but PDFium accepts
            # fractional scales (200 DPI == 200/72).
            bitmap = page.render(scale=_scale_for_page(page, dpi))  # type: ignore[arg-type]
            try:
                yield _flatten(bitmap.to_pil())
            finally:
                bitmap.close()
    finally:
        document.close()


def _save_image(image: Image.Image, output_file, target_format: str) -> None:
    """Encode ``image`` into ``output_file`` (a path or a binary file object)."""
    pillow_format = FORMAT_BY_EXT[target_format]
    if pillow_format == "JPEG":
        image.save(output_file, format=pillow_format, quality=JPEG_QUALITY)
    elif pillow_format == "WEBP":
        image.save(output_file, format=pillow_format, quality=WEBP_QUALITY)
    else:
        image.save(output_file, format=pillow_format)


def _same_canvas(frames: list[Image.Image]) -> list[Image.Image]:
    """Centre every frame on a shared white canvas (GIF frames must match)."""
    width = max(frame.width for frame in frames)
    height = max(frame.height for frame in frames)
    if all((frame.width, frame.height) == (width, height) for frame in frames):
        return frames

    padded: list[Image.Image] = []
    for frame in frames:
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(frame, ((width - frame.width) // 2, (height - frame.height) // 2))
        padded.append(canvas)
    return padded


def _write_multi_frame_pages(
    input_file: str, output_file: str, target_format: str, dpi: int
) -> int:
    """Write every page of a PDF into one multi-frame file; return page count."""
    frames = list(_render_pages(input_file, dpi))
    if not frames:
        raise RuntimeError(f"{input_file} contains no pages to convert")

    pillow_format = FORMAT_BY_EXT[target_format]
    if pillow_format == "GIF":
        frames = _same_canvas(frames)
        frames[0].save(
            output_file,
            format=pillow_format,
            save_all=True,
            append_images=frames[1:],
            duration=GIF_PAGE_DURATION_MS,
            loop=0,
        )
    else:  # TIFF
        frames[0].save(
            output_file,
            format=pillow_format,
            save_all=True,
            append_images=frames[1:],
            compression="tiff_deflate",
        )
    return len(frames)


def _write_page_images_archive(
    input_file: str, output_file: str, target_format: str, dpi: int
) -> int:
    """Write one page image per zip entry; return page count.

    Pages are encoded one at a time so a long document never holds more than a
    single decoded page bitmap in memory.
    """
    stem = Path(input_file).stem or "page"
    count = 0
    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for count, image in enumerate(_render_pages(input_file, dpi), start=1):
            buffer = io.BytesIO()
            _save_image(image, buffer, target_format)
            archive.writestr(f"{stem}_page_{count:03d}.{target_format}", buffer.getvalue())

    if count == 0:
        raise RuntimeError(f"{input_file} contains no pages to convert")
    return count


def _convert_pdf_to_image(input_file: str, output_file: str, target_format: str) -> None:
    """Convert ``input_file`` (PDF) to ``target_format`` at ``output_file``."""
    page_count = _page_count(input_file)
    if page_count == 0:
        raise RuntimeError(f"{input_file} contains no pages to convert")

    if target_format in MULTI_FRAME_TARGETS:
        written = _write_multi_frame_pages(input_file, output_file, target_format, DEFAULT_DPI)
        logger.debug("Rendered %s page(s) of %s into one %s", written, input_file, target_format)
        return

    if target_format not in ARCHIVE_TARGETS:  # pragma: no cover - guarded at registration
        raise RuntimeError(f"Unsupported PDF image target format: {target_format}")

    if page_count == 1:
        for image in _render_pages(input_file, DEFAULT_DPI):
            _save_image(image, output_file, target_format)
            break
        logger.debug("Rendered single page of %s to %s", input_file, target_format)
        return

    written = _write_page_images_archive(input_file, output_file, target_format, DEFAULT_DPI)
    logger.debug(
        "Rendered %s page(s) of %s into a .zip of %s images", written, input_file, target_format
    )


def _output_extension(input_file: str) -> str | None:
    """Declared output extension: ``zip`` for multi-page PDFs, else the target.

    Consumed by the worker through ``converter_registry.converter_output_extension``
    so the produced archive is stored and downloaded as ``*.zip``.
    """
    try:
        if _page_count(input_file) > 1:
            return "zip"
    except Exception as error:  # unreadable/corrupt PDF: the converter reports it
        logger.debug("Could not read page count of %s: %s", input_file, error)
    return None


def _make_pdf_image_converter(target_format: str):
    """Build a pdf -> ``target_format`` converter, with its output-extension hook."""

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _convert_pdf_to_image(input_file, output_file, target_format)

    converter.__name__ = f"pdf_to_{target_format}"
    if target_format in ARCHIVE_TARGETS:
        # Only the targets that bundle page images need the container hook;
        # TIFF/GIF carry their pages natively and keep the target extension.
        converter.output_extension = _output_extension  # type: ignore[attr-defined]
    return converter


for _target_format in FORMAT_BY_EXT:
    registry.register(ConversionType("pdf", _target_format))(
        _make_pdf_image_converter(_target_format)
    )
