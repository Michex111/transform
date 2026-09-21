"""Tests for the converter registry with all converter types."""

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import get_registry


class TestConverterRegistryCoverage:
    """Verify all expected converters are registered."""

    DOCUMENT_CONVERSIONS = [
        ("pdf", "docx"),
        ("docx", "pdf"),
        ("pdf", "odt"),
        ("odt", "pdf"),
        ("docx", "odt"),
        ("doc", "docx"),
        ("rtf", "pdf"),
        ("txt", "pdf"),
        ("md", "pdf"),
        ("pdf", "md"),
    ]

    AUDIO_CONVERSIONS = [
        ("mp3", "wav"),
        ("wav", "mp3"),
        ("wav", "flac"),
        ("flac", "wav"),
        ("mp3", "ogg"),
        ("ogg", "mp3"),
        ("mp3", "m4a"),
        ("m4a", "mp3"),
        ("wav", "aac"),
        ("aiff", "wav"),
        ("alac", "mp3"),
        ("m4b", "m4a"),
        ("opus", "wav"),
        ("wma", "mp3"),
        ("flac", "ogg"),
    ]

    VIDEO_CONVERSIONS = [
        ("mp4", "avi"),
        ("avi", "mp4"),
        ("mp4", "mov"),
        ("mov", "mp4"),
        ("avi", "mkv"),
        ("mkv", "avi"),
        ("mp4", "gif"),
        ("mp4", "webm"),
        ("webm", "mp4"),
        ("mp4", "flv"),
        ("wmv", "mp4"),
        ("mpg", "mp4"),
        ("m4v", "mov"),
        ("3gp", "mp4"),
    ]

    IMAGE_CONVERSIONS = [
        ("jpeg", "png"),
        ("png", "jpeg"),
        ("jpg", "png"),
        ("png", "jpg"),
        ("png", "webp"),
        ("webp", "png"),
        ("svg", "png"),
        ("png", "bmp"),
        ("bmp", "png"),
        ("png", "tiff"),
        ("png", "ico"),
        ("png", "avif"),
        ("jpg", "webp"),
        ("gif", "png"),
    ]

    PDF_IMAGE_CONVERSIONS = [
        ("pdf", "png"),
        ("pdf", "jpg"),
        ("pdf", "jpeg"),
        ("pdf", "webp"),
        ("pdf", "bmp"),
        ("pdf", "tiff"),
        ("pdf", "gif"),
    ]

    IMAGE_PDF_CONVERSIONS = [
        ("jpg", "pdf"),
        ("jpeg", "pdf"),
        ("png", "pdf"),
        ("webp", "pdf"),
        ("gif", "pdf"),
        ("bmp", "pdf"),
        ("tiff", "pdf"),
        ("ico", "pdf"),
        ("avif", "pdf"),
        ("svg", "pdf"),
    ]

    EBOOK_CONVERSIONS = [
        ("epub", "pdf"),
        ("pdf", "epub"),
        ("epub", "mobi"),
        ("mobi", "epub"),
        ("epub", "txt"),
        ("azw", "epub"),
        ("azw3", "mobi"),
        ("fb2", "epub"),
        ("lit", "epub"),
        ("epub", "azw3"),
    ]

    ARCHIVE_CONVERSIONS = [
        ("zip", "tar"),
        ("tar", "zip"),
        ("zip", "tar.gz"),
        ("tar.gz", "zip"),
        ("tar", "tar.gz"),
        ("gz", "bz2"),
        ("xz", "gz"),
    ]

    FONT_CONVERSIONS = [
        ("ttf", "woff"),
        ("woff", "ttf"),
        ("ttf", "woff2"),
        ("woff2", "ttf"),
        ("otf", "ttf"),
        ("eot", "ttf"),
        ("woff", "woff2"),
    ]

    OFFICE_CONVERSIONS = [
        ("xls", "xlsx"),
        ("xlsx", "csv"),
        ("csv", "xlsx"),
        ("ods", "xlsx"),
        ("ppt", "pptx"),
        ("pptx", "pdf"),
        ("odp", "pptx"),
        ("docx", "rtf"),
        ("odt", "docx"),
    ]

    def test_all_document_converters_registered(self):
        registry = get_registry()
        for source, target in self.DOCUMENT_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_audio_converters_registered(self):
        registry = get_registry()
        for source, target in self.AUDIO_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_video_converters_registered(self):
        registry = get_registry()
        for source, target in self.VIDEO_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_image_converters_registered(self):
        registry = get_registry()
        for source, target in self.IMAGE_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_pdf_image_converters_registered(self):
        registry = get_registry()
        for source, target in self.PDF_IMAGE_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_image_pdf_converters_registered(self):
        registry = get_registry()
        for source, target in self.IMAGE_PDF_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_ebook_converters_registered(self):
        registry = get_registry()
        for source, target in self.EBOOK_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_archive_converters_registered(self):
        registry = get_registry()
        for source, target in self.ARCHIVE_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_font_converters_registered(self):
        registry = get_registry()
        for source, target in self.FONT_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_all_office_converters_registered(self):
        registry = get_registry()
        for source, target in self.OFFICE_CONVERSIONS:
            ct = ConversionType(source, target)
            converter = registry.get_converter(ct)
            assert converter is not None, f"Missing converter: {source} -> {target}"

    def test_total_converter_count(self):
        """Verify minimum expected number of converters."""
        registry = get_registry()
        conversions = registry.list_conversions()
        min_expected = (
            len(self.DOCUMENT_CONVERSIONS)
            + len(self.AUDIO_CONVERSIONS)
            + len(self.VIDEO_CONVERSIONS)
            + len(self.IMAGE_CONVERSIONS)
            + len(self.PDF_IMAGE_CONVERSIONS)
            + len(self.IMAGE_PDF_CONVERSIONS)
            + len(self.EBOOK_CONVERSIONS)
            + len(self.ARCHIVE_CONVERSIONS)
            + len(self.FONT_CONVERSIONS)
            + len(self.OFFICE_CONVERSIONS)
        )
        assert len(conversions) >= min_expected, (
            f"Expected at least {min_expected} converters, got {len(conversions)}"
        )

    def test_unsupported_conversion_returns_none(self):
        registry = get_registry()
        ct = ConversionType("unsupported", "format")
        converter = registry.get_converter(ct)
        assert converter is None

    def test_find_output_handles_expected_stem(self, tmp_path):
        from src.infrastructure.converters.functions.office.office_converters import _find_output

        src = tmp_path / "report.pdf"
        src.write_bytes(b"pdf")
        (tmp_path / "report.odp").write_bytes(b"odp")
        found = _find_output(tmp_path, src, "odp")
        assert found is not None and found.name == "report.odp"

    def test_find_output_handles_basename_variant(self, tmp_path):
        from src.infrastructure.converters.functions.office.office_converters import _find_output

        src = tmp_path / "my.file.pdf"
        src.write_bytes(b"pdf")
        (tmp_path / "my.file.odp").write_bytes(b"odp")
        found = _find_output(tmp_path, src, "odp")
        assert found is not None and found.name == "my.file.odp"

    def test_find_output_returns_none_when_missing(self, tmp_path):
        from src.infrastructure.converters.functions.office.office_converters import _find_output

        src = tmp_path / "report.pdf"
        src.write_bytes(b"pdf")
        assert _find_output(tmp_path, src, "odp") is None

    def test_conversion_map_groups_targets_by_source(self):
        from src.infrastructure.converters.conversion_map import build_conversion_map

        mapping = build_conversion_map()
        # Known source formats present with sorted, non-empty target lists.
        assert "pdf" in mapping
        assert "docx" in mapping["pdf"]
        assert "epub" in mapping["pdf"]
        # pdf rasterises to images as well (pypdfium2).
        for image_target in ["png", "jpg", "webp", "bmp", "tiff", "gif"]:
            assert image_target in mapping["pdf"], f"pdf -> {image_target} missing"
        # ...and images go back into a PDF (Pillow / cairosvg).
        for image_source in ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "ico", "avif", "svg"]:
            assert "pdf" in mapping[image_source], f"{image_source} -> pdf missing"
        assert "xlsx" in mapping
        assert "csv" in mapping["xlsx"]
        assert "ods" in mapping["xlsx"]
        # mp3 can reach every other audio format.
        for target in ["wav", "flac", "ogg", "m4a", "opus", "wma", "aac", "aiff"]:
            assert target in mapping["mp3"], f"mp3 -> {target} missing"
        # Targets are sorted and deduplicated.
        assert mapping["pdf"] == sorted(set(mapping["pdf"]))
        # No source maps to itself.
        for source, targets in mapping.items():
            assert source not in targets
        # Every listed target round-trips through the registry.
        registry = get_registry()
        for source, targets in mapping.items():
            for target in targets:
                assert registry.get_converter(ConversionType(source, target)) is not None
