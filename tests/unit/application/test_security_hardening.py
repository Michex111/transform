"""Regression tests for production-readiness security/payment hardening fixes."""

from src.application.services.file_magic import detect_type, validate_upload_signature
from src.infrastructure.converters.functions.archive.archive_converters import (
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_EXPAND_BYTES,
    _extract,
)
from src.presentation.api.routers.v1.webhooks import _resolve_tier
from src.domain.subscriptions.value_object.tier import SubscriptionTier


# ---------------------------------------------------------------------------
# Magic-byte validation (F2)
# ---------------------------------------------------------------------------

def test_detect_type_recognizes_common_signatures() -> None:
    assert detect_type(b"%PDF-1.7 ...") == "pdf"
    assert detect_type(b"\x89PNG\r\n\x1a\n...") == "png"
    assert detect_type(b"\xff\xd8\xff\xe0...") == "jpeg"
    assert detect_type(b"PK\x03\x04...") == "zip"
    assert detect_type(b"GIF89a...") == "gif"
    assert detect_type(b"ID3\x04...") == "mp3"


def test_validate_upload_signature_accepts_matching_extension() -> None:
    assert validate_upload_signature(b"%PDF-1.7...", "pdf") is True
    assert validate_upload_signature(b"\x89PNG\r\n\x1a\n...", "png") is True


def test_validate_upload_signature_rejects_clear_mismatch() -> None:
    # A file claiming to be PDF but starting with PNG magic must be rejected.
    assert validate_upload_signature(b"\x89PNG\r\n\x1a\n...", "pdf") is False
    assert validate_upload_signature(b"PK\x03\x04...", "pdf") is False


def test_validate_upload_signature_is_lenient_for_unknown_or_ambiguous() -> None:
    # Text/unknown bytes and image extensions in the allowlist are not blocked.
    assert validate_upload_signature(b"just some text", "txt") is True
    assert validate_upload_signature(b"", "txt") is True
    assert validate_upload_signature(b"\x89PNG...", "webp") is True


def test_validate_upload_signature_accepts_alias_extensions() -> None:
    assert validate_upload_signature(b"\xff\xd8\xff\xe0...", "jpg") is True
    assert validate_upload_signature(b"\x00\x00\x00\x18ftyp...", "mov") is True


def test_detect_type_distinguishes_riff_containers() -> None:
    # RIFF carries both WAV audio and WebP images; offset 8 tells them apart.
    assert detect_type(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == "webp"
    assert detect_type(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "wav"


def test_validate_upload_signature_accepts_webp_images() -> None:
    # Regression: a real WebP was reported as WAV and rejected on upload, which
    # blocked every WebP conversion (including WebP → PDF).
    assert validate_upload_signature(b"RIFF\x24\x00\x00\x00WEBPVP8 ", "webp") is True
    assert validate_upload_signature(b"RIFF\x24\x00\x00\x00WEBPVP8 ", "wav") is False


def test_detect_type_recognizes_iso_bmff_brands() -> None:
    # mp4/mov/avif/heic share the ftyp header; only the brand separates them.
    assert detect_type(b"\x00\x00\x00 ftypavif") == "avif"
    assert detect_type(b"\x00\x00\x00 ftypisom") == "mp4"
    assert detect_type(b"\x00\x00\x00\x14ftypheic") == "heic"


def test_validate_upload_signature_accepts_avif_images() -> None:
    # Regression: AVIF is ISO BMFF, so it was reported as mp4 and rejected,
    # which blocked every AVIF conversion (including AVIF → PDF).
    assert validate_upload_signature(b"\x00\x00\x00 ftypavif", "avif") is True
    assert validate_upload_signature(b"\x00\x00\x00 ftypisom", "avif") is False


def test_validate_upload_signature_accepts_svg() -> None:
    # Regression: SVG is XML, and the generic xml heuristic rejected it.
    assert validate_upload_signature(b'<svg xmlns="http://www.w3.org/2000/svg"', "svg") is True


def test_validate_upload_signature_accepts_every_image_source() -> None:
    """Every image format the converter registry accepts must survive upload.

    A rejection here happens *before* the worker runs, so it silently makes a
    registered, advertised conversion impossible.
    """
    heads = {
        "jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
        "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
        "png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\r",
        "webp": b"RIFFN\x00\x00\x00WEBP",
        "gif": b"GIF87a<\x00(\x00",
        "bmp": b"BMV\x1c\x00\x00\x00",
        "tiff": b"II*\x00\x08\x00\x00\x00",
        "ico": b"\x00\x00\x01\x00\x05\x00",
        "avif": b"\x00\x00\x00 ftypavif",
        "svg": b'<svg xmlns="http://www.w3.org/2000/svg"',
    }
    for extension, head in heads.items():
        assert validate_upload_signature(head, extension) is True, extension


def test_validate_upload_signature_accepts_every_advertised_audio_format() -> None:
    """Every audio extension the format catalog advertises must survive upload.

    The same class of bug as the image case above: this check runs *before* the
    worker, so a rejection silently deletes the object and makes an advertised
    format impossible to upload. Three formats were in exactly that state —
    ``.opus``, ``.alac`` and ``.m4b`` — because ``detect_type`` reports the
    *container* (``ogg`` for Opus, ``mp4`` for ALAC and the M4B audiobook) and
    the alias table did not list those extensions under the container's label.
    Each head here is the real leading bytes of a file produced by ffmpeg.
    """
    heads = {
        "mp3": b"ID3\x04\x00\x00\x00\x00\x00\x22TSSE",  # ID3v2 tag
        "wav": b"RIFF\xa2g\x00\x00WAVEfmt ",
        "flac": b"fLaC\x00\x00\x00\x22\x12\x00\x12\x00\x00",
        "ogg": b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        # Ogg container, non-Vorbis payloads.
        "oga": b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        "opus": b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        "spx": b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00",
        # ISO BMFF / MP4 container.
        "m4a": b"\x00\x00\x00\x1cftypM4A \x00\x00\x02\x00",
        "m4b": b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00",
        "alac": b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00",
        # No recognised signature at all — lenient, must still pass.
        "aac": b"\xff\xf1P@!?\xfc\xde\x02\x00Lavc",
        "aiff": b"FORM\x00\x00g\x8aAIFFCO",
        "wma": b"0&\xb2u\x8ef\xcf\x11\xa6\xd9\x00\xaa\x00b",
        "mid": b"MThd\x00\x00\x00\x06\x00\x00",
    }
    for extension, head in heads.items():
        assert validate_upload_signature(head, extension) is True, extension


def test_ogg_and_mp4_containers_reject_a_genuine_mismatch() -> None:
    """Widening those alias sets must not turn the check off.

    Accepting ``.opus``/``.alac`` is only correct because those really are Ogg
    and MP4 containers. A file whose bytes are a *different* container must
    still be refused, otherwise the alias table has quietly become "always say
    yes" and the whole extension-spoofing guard is gone.
    """
    ogg = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"
    mp4 = b"\x00\x00\x00\x1cftypM4A \x00\x00\x02\x00"
    pdf = b"%PDF-1.7\n"

    assert validate_upload_signature(ogg, "opus") is True
    assert validate_upload_signature(ogg, "mp3") is False
    assert validate_upload_signature(mp4, "alac") is True
    assert validate_upload_signature(mp4, "flac") is False
    # The pre-existing spoofing case is untouched.
    assert validate_upload_signature(pdf, "mp3") is False


# ---------------------------------------------------------------------------
# Archive decompression-bomb guard (F3)
# ---------------------------------------------------------------------------

def test_archive_limits_are_positive_and_bounded() -> None:
    assert MAX_ARCHIVE_ENTRIES > 0
    assert MAX_ARCHIVE_EXPAND_BYTES > 0


def test_extract_rejects_zip_bomb(tmp_path) -> None:
    import zipfile
    bomb_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # A single member larger than the cap.
        zf.writestr("big.bin", b"\x00" * (MAX_ARCHIVE_EXPAND_BYTES + 1))
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        _extract(str(bomb_path), str(dest))
        raise AssertionError("Expected RuntimeError for oversized archive member")
    except RuntimeError as exc:
        assert "zip-bomb" in str(exc) or "too large" in str(exc) or "too many" in str(exc)


def test_extract_rejects_too_many_entries(tmp_path) -> None:
    import zipfile
    bomb_path = tmp_path / "many.zip"
    with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for _ in range(MAX_ARCHIVE_ENTRIES + 1):
            zf.writestr("f", b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    try:
        _extract(str(bomb_path), str(dest))
        raise AssertionError("Expected RuntimeError for too many archive members")
    except RuntimeError as exc:
        assert "too many" in str(exc)


# ---------------------------------------------------------------------------
# Webhook strict tier resolution (B2)
# ---------------------------------------------------------------------------

def test_resolve_tier_strictly_maps_known_tiers() -> None:
    assert _resolve_tier("pro") is SubscriptionTier.PRO
    assert _resolve_tier("PRO_PLUS") is SubscriptionTier.PRO_PLUS
    assert _resolve_tier("enterprise") is SubscriptionTier.ENTERPRISE


def test_resolve_tier_rejects_unknown_tier() -> None:
    import pytest
    with pytest.raises(ValueError):
        _resolve_tier("not_a_tier")
    with pytest.raises(ValueError):
        _resolve_tier(None)
