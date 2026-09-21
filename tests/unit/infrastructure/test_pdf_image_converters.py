"""Tests for the pdf -> raster image converters (pypdfium2 + Pillow).

PDF fixtures are generated with Pillow's PDF writer, so the tests need no
binary fixture files and no LibreOffice/poppler installation.
"""

import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import (
    converter_output_extension,
    get_registry,
)
from src.infrastructure.converters.functions.document import pdf_image_converters as pdf_image

TARGETS = ["png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"]
ARCHIVE_TARGETS = ["png", "jpg", "jpeg", "webp", "bmp"]
MULTI_FRAME_TARGETS = ["tiff", "gif"]


def _write_pdf(path: Path, page_count: int, size: tuple[int, int] = (120, 180)) -> Path:
    """Write a ``page_count``-page PDF using Pillow's PDF writer."""
    pages = [Image.new("RGB", size, (i * 40 % 255, 90, 200)) for i in range(page_count)]
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    return path


@pytest.fixture
def single_page_pdf(tmp_path: Path) -> Path:
    return _write_pdf(tmp_path / "single.pdf", 1)


@pytest.fixture
def multi_page_pdf(tmp_path: Path) -> Path:
    return _write_pdf(tmp_path / "multi.pdf", 3)


def _converter(target_format: str):
    converter = get_registry().get_converter(ConversionType("pdf", target_format))
    assert converter is not None, f"pdf -> {target_format} is not registered"
    return converter


def _is_zip(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(2) == b"PK"


class TestRegistration:
    def test_every_raster_target_is_registered(self):
        registry = get_registry()
        for target in TARGETS:
            assert registry.get_converter(ConversionType("pdf", target)) is not None

    def test_targets_appear_in_the_conversion_map(self):
        from src.infrastructure.converters.conversion_map import build_conversion_map

        for target in TARGETS:
            assert target in build_conversion_map()["pdf"]


class TestSinglePagePdf:
    @pytest.mark.parametrize("target", TARGETS)
    def test_single_page_writes_one_plain_image(self, single_page_pdf: Path, target: str, tmp_path: Path):
        output = tmp_path / f"out.{target}"
        _converter(target)(str(single_page_pdf), str(output))

        assert output.is_file() and output.stat().st_size > 0
        assert not _is_zip(output)
        with Image.open(output) as image:
            assert getattr(image, "n_frames", 1) == 1
            assert image.width > 0 and image.height > 0

    def test_jpeg_output_is_rgb(self, single_page_pdf: Path, tmp_path: Path):
        output = tmp_path / "out.jpg"
        _converter("jpg")(str(single_page_pdf), str(output))

        with Image.open(output) as image:
            assert image.format == "JPEG"
            assert image.mode == "RGB"

    def test_png_is_rendered_at_the_default_dpi(self, single_page_pdf: Path, tmp_path: Path):
        """A 120x180pt page at 200 DPI is ~333x500 px (200/72 scaling)."""
        output = tmp_path / "out.png"
        _converter("png")(str(single_page_pdf), str(output))

        with Image.open(output) as image:
            expected_width = round(120 * pdf_image.DEFAULT_DPI / 72)
            expected_height = round(180 * pdf_image.DEFAULT_DPI / 72)
            assert abs(image.width - expected_width) <= 1
            assert abs(image.height - expected_height) <= 1


class TestMultiPagePdf:
    @pytest.mark.parametrize("target", ARCHIVE_TARGETS)
    def test_pages_are_bundled_into_a_zip(self, multi_page_pdf: Path, target: str, tmp_path: Path):
        output = tmp_path / f"out.{target}"
        _converter(target)(str(multi_page_pdf), str(output))

        assert _is_zip(output)
        with zipfile.ZipFile(output) as archive:
            assert archive.namelist() == [
                f"multi_page_00{index}.{target}" for index in range(1, 4)
            ]
            with archive.open(archive.namelist()[0]) as member:
                with Image.open(member) as image:
                    assert image.width > 0

    @pytest.mark.parametrize("target", MULTI_FRAME_TARGETS)
    def test_pages_fill_one_multi_frame_file(self, multi_page_pdf: Path, target: str, tmp_path: Path):
        output = tmp_path / f"out.{target}"
        _converter(target)(str(multi_page_pdf), str(output))

        assert not _is_zip(output)
        with Image.open(output) as image:
            assert getattr(image, "n_frames", 1) == 3

    def test_archive_members_are_valid_images(self, multi_page_pdf: Path, tmp_path: Path):
        output = tmp_path / "out.png"
        _converter("png")(str(multi_page_pdf), str(output))

        with zipfile.ZipFile(output) as archive:
            for name in archive.namelist():
                with Image.open(archive.open(name)) as image:
                    assert image.format == "PNG"

    def test_tiff_streams_pages_instead_of_materialising_them(
        self, multi_page_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A multi-page TIFF must not hold every decoded page in memory.

        Materialising a long document costs ~11.6 MB per A4 page at 200 DPI
        (~3.5 GB for 300 pages), which has OOMed the worker. Only the first page
        may be rendered before the writer starts; the rest stream into it.
        """
        rendered: list[int] = []
        pulls_when_save_started: list[int] = []

        def counting_render(pdf_path, dpi):
            for index in range(5):
                rendered.append(index)
                yield Image.new("RGB", (8, 8), (index, 0, 0))

        monkeypatch.setattr(pdf_image, "_render_pages", counting_render)

        original_save = Image.Image.save

        def spy_save(self, fp, *args, **kwargs):
            if kwargs.get("save_all"):
                pulls_when_save_started.append(len(rendered))
            return original_save(self, fp, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "save", spy_save)

        output = tmp_path / "streamed.tiff"
        assert pdf_image._write_multi_frame_pages(str(multi_page_pdf), str(output), "tiff", 72) == 5

        assert len(rendered) == 5, "every page must still be written"
        assert pulls_when_save_started == [1], (
            "the TIFF writer must receive the remaining pages lazily; rendering "
            f"all of them up front means the whole document is resident (saw {pulls_when_save_started})"
        )

    def test_tiff_output_is_identical_to_the_materialised_version(
        self, multi_page_pdf: Path, tmp_path: Path
    ):
        """Streaming must not change a single byte of the produced file."""
        streamed = tmp_path / "streamed.tiff"
        pdf_image._write_multi_frame_pages(str(multi_page_pdf), str(streamed), "tiff", pdf_image.DEFAULT_DPI)

        # Reference: the previous implementation, which built a full list.
        frames = list(pdf_image._render_pages(str(multi_page_pdf), pdf_image.DEFAULT_DPI))
        materialised = tmp_path / "materialised.tiff"
        frames[0].save(
            materialised,
            format="TIFF",
            save_all=True,
            append_images=frames[1:],
            compression="tiff_deflate",
        )

        assert streamed.read_bytes() == materialised.read_bytes()


class TestOutputExtensionHook:
    def test_single_page_keeps_the_target_extension(self, single_page_pdf: Path):
        converter = _converter("png")
        assert converter_output_extension(converter, "png", str(single_page_pdf)) == "png"

    def test_multi_page_declares_a_zip_container(self, multi_page_pdf: Path):
        converter = _converter("png")
        assert converter_output_extension(converter, "png", str(multi_page_pdf)) == "zip"

    @pytest.mark.parametrize("target", MULTI_FRAME_TARGETS)
    def test_multi_frame_targets_do_not_use_a_container(
        self, multi_page_pdf: Path, target: str
    ):
        converter = _converter(target)
        assert converter_output_extension(converter, target, str(multi_page_pdf)) == target

    def test_unreadable_pdf_keeps_the_target_extension(self, tmp_path: Path):
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not really a pdf")
        converter = _converter("png")

        assert converter_output_extension(converter, "png", str(broken)) == "png"


class TestRenderingGuards:
    def test_oversized_pages_are_scaled_down(self, tmp_path: Path):
        """A page wider than MAX_RENDER_SIDE px is downscaled instead of
        allocating an unbounded bitmap (Pillow writes 1pt per pixel by default,
        so a 5000px page is a 5000pt page = ~13889px at 200 DPI)."""
        huge = tmp_path / "huge.pdf"
        _write_pdf(huge, 1, size=(5000, 300))
        output = tmp_path / "huge.png"

        _converter("png")(str(huge), str(output))

        with Image.open(output) as image:
            assert max(image.size) == pdf_image.MAX_RENDER_SIDE

    def test_invalid_pdf_is_rejected(self, tmp_path: Path):
        """An unreadable PDF fails the job with PDFium's load error."""
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"%PDF-1.4\n%%EOF\n")

        with pytest.raises(pdfium.PdfiumError, match="Failed to load document"):
            _converter("png")(str(empty), str(tmp_path / "out.png"))

    def test_pdf_without_pages_is_rejected(
        self, single_page_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A document that reports zero pages never writes an empty output."""
        monkeypatch.setattr(pdf_image, "_page_count", lambda _path: 0)

        with pytest.raises(RuntimeError, match="no pages"):
            _converter("png")(str(single_page_pdf), str(tmp_path / "out.png"))
