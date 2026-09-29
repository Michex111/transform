"""Tests for the extension → MIME type table and the upload-time derivation.

These pin the behaviour that replaced "store whatever the object store said".
The value that used to be recorded was the *upload's* leftover, not the file's:
the single-PUT path signs its presigned URL without a ``Content-Type`` (sending
one breaks the signature), so the provider substituted its own default and the
API stored that. Measured against the live bucket: every single-PUT object came
back as ``application/x-www-form-urlencoded`` and every multipart object as
``application/octet-stream``. Both are meaningless, and the first was actively
harmful — it made the streamed download advertise a form submission for a song.
"""

import pytest

from src.application.services.file_service import FileService
from src.application.services.mime_types import (
    FALLBACK_MIME_TYPE,
    GENERIC_MIME_TYPES,
    mime_type_for,
)


# ---------------------------------------------------------------------------
# mime_type_for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        ("pdf", "application/pdf"),
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("webp", "image/webp"),
        ("svg", "image/svg+xml"),
        ("avif", "image/avif"),
        ("heic", "image/heic"),
        ("tiff", "image/tiff"),
        ("mp4", "video/mp4"),
        ("webm", "video/webm"),
        ("mov", "video/quicktime"),
        ("mkv", "video/x-matroska"),
        ("ogv", "video/ogg"),
        ("wmv", "video/x-ms-wmv"),
        ("flv", "video/x-flv"),
        ("mpg", "video/mpeg"),
        ("3gp", "video/3gpp"),
        ("mp3", "audio/mpeg"),
        ("wav", "audio/wav"),
        ("flac", "audio/flac"),
        ("m4a", "audio/mp4"),
        ("aac", "audio/aac"),
        # Ogg Opus is labelled as the Ogg container it actually is.
        ("opus", "audio/ogg"),
        ("oga", "audio/ogg"),
        # ALAC and the M4B audiobook are MP4 containers.
        ("alac", "audio/mp4"),
        ("m4b", "audio/mp4"),
        ("aiff", "audio/aiff"),
        ("wma", "audio/x-ms-wma"),
        ("mid", "audio/midi"),
        ("txt", "text/plain"),
        ("csv", "text/csv"),
        ("json", "application/json"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("zip", "application/zip"),
        ("epub", "application/epub+zip"),
    ],
)
def test_mime_type_for_known_extensions(extension: str, expected: str) -> None:
    assert mime_type_for(extension) == expected


def test_mime_type_for_normalises_the_extension() -> None:
    # With a dot, any case, and surrounding whitespace all resolve the same.
    assert mime_type_for(".PDF") == "application/pdf"
    assert mime_type_for("  Mp3 ") == "audio/mpeg"
    assert mime_type_for(".OpUs") == "audio/ogg"


def test_mime_type_for_resolves_a_compound_extension_on_its_outer_layer() -> None:
    # `extension_from_filename` keeps recognised archives compound, so these
    # reach the table as "tar.gz" rather than "gz". The outer layer is what a
    # download or an inline render needs, so it is what we answer with.
    assert mime_type_for("tar.gz") == "application/gzip"
    assert mime_type_for("tar.bz2") == "application/x-bzip2"
    assert mime_type_for("tar.xz") == "application/x-xz"
    # Still `None` when the outer layer is unknown too — no partial guess.
    assert mime_type_for("backup.unknownext") is None


def test_mime_type_for_returns_none_when_unknown() -> None:
    # None rather than a guess: the caller decides the fallback, and a caller
    # that would rather keep the provider's value must be able to tell the
    # difference between "we know nothing" and "it is octet-stream".
    assert mime_type_for("xyz") is None
    assert mime_type_for("") is None
    assert mime_type_for(".") is None
    assert mime_type_for(None) is None  # type: ignore[arg-type]


def test_generic_mime_types_cover_what_storage_actually_reports() -> None:
    # Both of these are real values observed on the live backblaze bucket for
    # an untyped PUT; if either stops being treated as generic, the file's
    # stored type regresses to it.
    assert "application/x-www-form-urlencoded" in GENERIC_MIME_TYPES
    assert FALLBACK_MIME_TYPE in GENERIC_MIME_TYPES


# ---------------------------------------------------------------------------
# FileService._resolve_mime_type
# ---------------------------------------------------------------------------
# Static and side-effect free, so it is called directly rather than through a
# fully wired service.

def resolve(file_name: str, extension: str, reported: str | None) -> str:
    return FileService._resolve_mime_type(file_name, extension, reported)


def test_extension_wins_over_the_provider_default() -> None:
    # THE regression this whole change exists for: an audio file whose object
    # store content type is the headerless-PUT default.
    assert resolve(
        "song.mp3", "mp3", "application/x-www-form-urlencoded"
    ) == "audio/mpeg"
    assert resolve("clip.mp4", "mp4", "application/octet-stream") == "video/mp4"
    assert resolve("doc.pdf", "pdf", "application/x-www-form-urlencoded") == "application/pdf"


def test_multipart_and_single_put_agree_on_the_same_file() -> None:
    # The two upload paths leave different defaults behind; the derived type
    # must not depend on which one was used, or the same song would be
    # described differently depending on its size.
    assert resolve("song.mp3", "mp3", "application/x-www-form-urlencoded") == resolve(
        "song.mp3", "mp3", "application/octet-stream"
    )


def test_falls_back_to_the_file_name_when_no_extension_was_recorded() -> None:
    # Legacy rows predate the declared-extension field.
    assert resolve("archive.tar.gz", "", None) == "application/gzip"
    assert resolve("photo.PNG", "", "application/octet-stream") == "image/png"


def test_keeps_a_specific_reported_type_for_an_unknown_extension() -> None:
    # A future upload path that does sign a real content type should not be
    # thrown away just because the name is unfamiliar.
    assert resolve("blob.dat", "dat", "application/x-custom-thing") == "application/x-custom-thing"


def test_never_records_a_generic_type_as_the_answer() -> None:
    # An unknown extension plus a meaningless reported type yields the explicit
    # fallback, not the provider's default echoed back as though it meant
    # something.
    assert resolve("data.xyz", "xyz", "application/x-www-form-urlencoded") == FALLBACK_MIME_TYPE
    assert resolve("data.xyz", "xyz", "binary/octet-stream") == FALLBACK_MIME_TYPE
    assert resolve("data.xyz", "xyz", "") == FALLBACK_MIME_TYPE
    assert resolve("data.xyz", "xyz", None) == FALLBACK_MIME_TYPE


def test_strips_a_charset_parameter_from_the_reported_type() -> None:
    # `text/plain; charset=utf-8` is stored as `text/plain`, so a comparison or
    # a header built from it is not carrying a bogus parameter.
    assert resolve("notes.unknownext", "unknownext", "text/plain; charset=utf-8") == "text/plain"
    assert resolve("data.xyz", "xyz", "application/x-www-form-urlencoded; charset=UTF-8") == FALLBACK_MIME_TYPE
