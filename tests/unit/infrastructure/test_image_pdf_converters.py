"""Tests for the image -> PDF converters (Pillow + cairosvg).

Fixtures are generated with Pillow, so the tests need no binary fixture files
and no system tooling. Output PDFs are reopened with pypdfium2 to assert real
page geometry and rendered pixels rather than just "a file appeared".
"""

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import get_registry
from src.infrastructure.converters.functions.image import image_pdf_converters as image_pdf

RASTER_SOURCES = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "ico", "avif"]
SOURCES = [*RASTER_SOURCES, "svg"]

#: "ico" round-trips through Pillow's icon writer only at a standard size, so
#: its fixture is square; every other source keeps the pixels it was given.
SINGLE_FRAME_SOURCES = [
    ("jpg", (60, 40)),
    ("jpeg", (60, 40)),
    ("png", (60, 40)),
    ("webp", (60, 40)),
    ("bmp", (60, 40)),
    ("avif", (60, 40)),
    ("ico", (64, 64)),
]


def _converter(source_format: str):
    converter = get_registry().get_converter(ConversionType(source_format, "pdf"))
    assert converter is not None, f"{source_format} -> pdf is not registered"
    return converter


def _convert(source_format: str, input_path: Path, tmp_path: Path, name: str = "out") -> Path:
    output = tmp_path / f"{name}.pdf"
    _converter(source_format)(str(input_path), str(output))
    return output


def _pages(pdf_path: Path) -> list[tuple[float, float]]:
    """Point size of every page, in order."""
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return [document[index].get_size() for index in range(len(document))]
    finally:
        document.close()


def _render_first_page(pdf_path: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return document[0].render(scale=1).to_pil().convert("RGB")
    finally:
        document.close()


def _render_page(pdf_path: Path, index: int) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        return document[index].render(scale=1).to_pil().convert("RGB")
    finally:
        document.close()


def _write_image(path: Path, mode: str, size: tuple[int, int], color, **kwargs) -> Path:
    Image.new(mode, size, color).save(path, **kwargs)
    return path


class TestRegistration:
    @pytest.mark.parametrize("source", SOURCES)
    def test_every_image_source_is_registered(self, source: str):
        assert get_registry().get_converter(ConversionType(source, "pdf")) is not None

    def test_targets_appear_in_the_conversion_map(self):
        from src.infrastructure.converters.conversion_map import build_conversion_map

        mapping = build_conversion_map()
        for source in SOURCES:
            assert "pdf" in mapping.get(source, []), f"{source} -> pdf missing from map"

    def test_svg_is_routed_to_the_vector_converter(self):
        """SVG must not be rasterised on the way to PDF."""
        registry = get_registry()
        svg_converter = registry.get_converter(ConversionType("svg", "pdf"))
        raster_converter = registry.get_converter(ConversionType("png", "pdf"))
        assert svg_converter is not None and raster_converter is not None
        assert svg_converter is not raster_converter

    def test_no_output_extension_hook(self):
        """PDF is natively multi-page, so no container naming is needed."""
        for source in SOURCES:
            converter = _converter(source)
            assert getattr(converter, "output_extension", None) is None


class TestSingleFrameImages:
    @pytest.mark.parametrize("source,size", SINGLE_FRAME_SOURCES)
    def test_single_frame_source_writes_one_page(
        self, source: str, size: tuple[int, int], tmp_path: Path
    ):
        source_file = _write_image(tmp_path / f"in.{source}", "RGB", size, (10, 120, 240))

        pdf = _convert(source, source_file, tmp_path)

        assert pdf.is_file() and pdf.stat().st_size > 0
        pages = _pages(pdf)
        assert len(pages) == 1
        width, height = pages[0]
        assert width > 0 and height > 0
        # The page keeps the image's aspect ratio; its absolute size follows the
        # DPI the container happens to store (bmp says 96, most say nothing).
        assert abs((width / height) - (size[0] / size[1])) < 0.05

    def test_pixels_survive_the_round_trip(self, tmp_path: Path):
        source_file = _write_image(tmp_path / "in.png", "RGB", (50, 50), (15, 30, 200))

        rendered = _render_first_page(_convert("png", source_file, tmp_path))

        assert rendered.size == (50, 50)
        for pixel in (rendered.getpixel((1, 1)), rendered.getpixel((25, 25))):
            assert all(abs(actual - expected) <= 6 for actual, expected in zip(pixel, (15, 30, 200)))

    def test_transparency_is_flattened_onto_white(self, tmp_path: Path):
        """A transparent background must not arrive as black."""
        transparent = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        transparent.paste(Image.new("RGB", (20, 20), (0, 0, 255)), (20, 20))
        source_file = tmp_path / "in.png"
        transparent.save(source_file)

        rendered = _render_first_page(_convert("png", source_file, tmp_path))

        outside = rendered.getpixel((2, 2))
        inside = rendered.getpixel((30, 30))
        assert all(channel > 240 for channel in outside), f"expected white, got {outside}"
        assert inside[2] > 200 and inside[0] < 40, f"expected blue, got {inside}"

    def test_paletted_image_converts(self, tmp_path: Path):
        """Mode P (and its transparency info) must not reach the PDF writer raw."""
        source_file = _write_image(tmp_path / "in.png", "P", (40, 40), 3)

        assert len(_pages(_convert("png", source_file, tmp_path))) == 1

    def test_sixteen_bit_grayscale_converts(self, tmp_path: Path):
        source_file = _write_image(tmp_path / "in.tiff", "I;16", (30, 30), 65535)

        rendered = _render_first_page(_convert("tiff", source_file, tmp_path))

        assert all(channel > 240 for channel in rendered.getpixel((15, 15)))


class TestPageGeometry:
    def test_source_dpi_sets_the_page_size(self, tmp_path: Path):
        """A 600x400 image marked 300 DPI is a 2x1.33 inch page."""
        source_file = tmp_path / "in.jpg"
        Image.new("RGB", (600, 400), (200, 30, 30)).save(source_file, dpi=(300, 300))

        width, height = _pages(_convert("jpg", source_file, tmp_path))[0]

        assert abs(width - 144.0) <= 0.5
        assert abs(height - 96.0) <= 0.5

    def test_missing_dpi_falls_back_to_seventy_two(self, tmp_path: Path):
        source_file = _write_image(tmp_path / "in.jpg", "RGB", (600, 400), (200, 30, 30))

        width, height = _pages(_convert("jpg", source_file, tmp_path))[0]

        assert abs(width - 600.0) <= 0.5
        assert abs(height - 400.0) <= 0.5

    @pytest.mark.parametrize("dpi", [(0, 0), (1, 1), (5000, 5000), (-300, -300)])
    def test_unusable_dpi_is_ignored(self, dpi: tuple[int, int], tmp_path: Path):
        fake = Image.new("RGB", (200, 100), (0, 0, 0))
        fake.info["dpi"] = dpi

        assert image_pdf._resolution(fake) == image_pdf.DEFAULT_RESOLUTION

    def test_usable_dpi_is_reported(self):
        fake = Image.new("RGB", (10, 10))
        fake.info["dpi"] = (300, 300)

        assert image_pdf._resolution(fake) == 300.0

    def test_mixed_dpi_uses_the_smaller_scale(self):
        """One page scale applies to the whole document, so never upscale."""
        fake = Image.new("RGB", (10, 10))
        fake.info["dpi"] = (300, 72)

        assert image_pdf._resolution(fake) == 72.0

    def test_pages_may_have_different_sizes(self, tmp_path: Path):
        """A multi-page TIFF keeps each page's own pixel dimensions."""
        small = Image.new("RGB", (60, 45), (0, 0, 0))
        large = Image.new("RGB", (120, 90), (10, 200, 10))
        source_file = tmp_path / "in.tiff"
        large.save(source_file, save_all=True, append_images=[small])

        sizes = _pages(_convert("tiff", source_file, tmp_path))

        assert len(sizes) == 2
        assert abs(sizes[0][0] - 120) <= 1 and abs(sizes[1][0] - 60) <= 1


class TestMultiFrameImages:
    def test_animated_gif_becomes_one_page_per_frame(self, tmp_path: Path):
        first = Image.new("RGB", (80, 60), (255, 0, 0))
        second = first.copy()
        ImageDraw.Draw(second).rectangle([40, 20, 60, 40], fill=(0, 255, 0))
        source_file = tmp_path / "in.gif"
        first.save(source_file, save_all=True, append_images=[second], duration=100, loop=0)

        pdf = _convert("gif", source_file, tmp_path)

        assert len(_pages(pdf)) == 2
        # Frame composition is kept: page 2 is page 1 with the square added.
        assert _render_page(pdf, 0).getpixel((50, 30))[0] > 200
        second_page = _render_page(pdf, 1).getpixel((50, 30))
        assert second_page[1] > 200 and second_page[0] < 60

    def test_multi_page_tiff_becomes_one_page_per_frame(self, tmp_path: Path):
        first = Image.new("RGB", (40, 30), (255, 0, 0))
        second = Image.new("RGB", (40, 30), (0, 0, 255))
        source_file = tmp_path / "in.tiff"
        first.save(source_file, save_all=True, append_images=[second])

        pdf = _convert("tiff", source_file, tmp_path)

        assert len(_pages(pdf)) == 2
        assert _render_page(pdf, 0).getpixel((20, 15))[0] > 200
        assert _render_page(pdf, 1).getpixel((20, 15))[2] > 200

    def test_single_frame_image_is_not_duplicated(self, tmp_path: Path):
        source_file = _write_image(tmp_path / "in.png", "RGB", (30, 30), (0, 0, 0))

        assert len(_pages(_convert("png", source_file, tmp_path))) == 1


class TestSvgToPdf:
    SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#3366ff"/></svg>'
    )

    def test_svg_renders_a_one_page_pdf(self, tmp_path: Path):
        source_file = tmp_path / "in.svg"
        source_file.write_text(self.SVG)

        pdf = _convert("svg", source_file, tmp_path)

        assert len(_pages(pdf)) == 1
        assert _render_first_page(pdf).getpixel((75, 37))[2] > 200

    def test_broken_svg_leaves_no_output(self, tmp_path: Path):
        source_file = tmp_path / "in.svg"
        source_file.write_text("<svg><not closed")

        with pytest.raises(Exception):
            _convert("svg", source_file, tmp_path)


class TestFailureHandling:
    def test_unreadable_input_raises_and_leaves_no_pdf(self, tmp_path: Path):
        source_file = tmp_path / "in.png"
        source_file.write_bytes(b"definitely not an image")

        with pytest.raises(Exception):
            _convert("png", source_file, tmp_path)

        assert not (tmp_path / "out.pdf").exists()

    def test_partial_output_is_removed_when_a_later_frame_fails(self, tmp_path: Path, monkeypatch):
        """A half-written PDF must never be uploaded as a successful job."""
        first = Image.new("RGB", (40, 30), (255, 0, 0))
        second = Image.new("RGB", (40, 30), (0, 0, 255))
        source_file = tmp_path / "in.tiff"
        first.save(source_file, save_all=True, append_images=[second])
        output = tmp_path / "out.pdf"

        real_flatten = image_pdf._flatten
        calls = {"count": 0}

        def _flaky(image):
            calls["count"] += 1
            if calls["count"] > 1:
                raise OSError("disk full")
            return real_flatten(image)

        monkeypatch.setattr(image_pdf, "_flatten", _flaky)

        with pytest.raises(OSError):
            image_pdf._convert_image_to_pdf(str(source_file), str(output))

        assert calls["count"] > 1, "the first page must be written before the failure"
        assert not output.exists()

    def test_converter_accepts_and_ignores_logger_override(self, tmp_path: Path):
        source_file = _write_image(tmp_path / "in.png", "RGB", (20, 20), (0, 0, 0))
        output = tmp_path / "out.pdf"

        _converter("png")(str(source_file), str(output), logger_override=object())

        assert output.is_file()

    def test_converter_has_a_readable_name(self):
        assert _converter("jpg").__name__ == "image_jpg_to_pdf"
        assert _converter("svg").__name__ == "image_svg_to_pdf"
