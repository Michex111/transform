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
