"""Tests for the converter registry with all converter types."""

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import get_registry


class TestConverterRegistryCoverage:
    """Verify all expected converters are registered."""

    DOCUMENT_CONVERSIONS = [
        ("pdf", "docx"),
        ("docx", "pdf"),
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
    ]

    VIDEO_CONVERSIONS = [
        ("mp4", "avi"),
        ("avi", "mp4"),
        ("mp4", "mov"),
        ("mov", "mp4"),
        ("avi", "mkv"),
        ("mkv", "avi"),
        ("mp4", "gif"),
    ]

    IMAGE_CONVERSIONS = [
        ("jpeg", "png"),
        ("png", "jpeg"),
        ("jpg", "png"),
        ("png", "jpg"),
        ("png", "webp"),
        ("webp", "png"),
        ("svg", "png"),
    ]

    EBOOK_CONVERSIONS = [
        ("epub", "pdf"),
        ("pdf", "epub"),
        ("epub", "mobi"),
        ("mobi", "epub"),
        ("epub", "txt"),
    ]

    ARCHIVE_CONVERSIONS = [
        ("zip", "tar"),
        ("tar", "zip"),
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

    def test_total_converter_count(self):
        """Verify minimum expected number of converters."""
        registry = get_registry()
        conversions = registry.list_conversions()
        min_expected = (
            len(self.DOCUMENT_CONVERSIONS)
            + len(self.AUDIO_CONVERSIONS)
            + len(self.VIDEO_CONVERSIONS)
            + len(self.IMAGE_CONVERSIONS)
            + len(self.EBOOK_CONVERSIONS)
            + len(self.ARCHIVE_CONVERSIONS)
        )
        assert len(conversions) >= min_expected, (
            f"Expected at least {min_expected} converters, got {len(conversions)}"
        )

    def test_unsupported_conversion_returns_none(self):
        registry = get_registry()
        ct = ConversionType("unsupported", "format")
        converter = registry.get_converter(ct)
        assert converter is None
