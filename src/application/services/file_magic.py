"""Lightweight magic-byte signature validation for uploaded files.

The upload flow trusts a client-supplied extension; a file claiming to be
``.pdf`` could actually be an executable or archive. This module inspects the
first bytes of a stored object (fetched from object storage) and verifies the
magic signature is consistent with the claimed extension. It uses only the
Python standard library (no new dependency). Unknown/ambiguous signatures are
treated leniently (rejected only on a clear mismatch) so legitimate files are
not blocked.

This is defense-in-depth: it is not a substitute for per-converter sandboxing
or the archive zip-bomb guard, but it prevents the most common extension-spoof
abuse before a converter (LibreOffice/ffmpeg/calibre) is fed a hostile file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Sig:
    """A magic-byte signature and the display name it implies."""

    prefixes: tuple[bytes, ...]
    label: str


# Common magic signatures we can cheaply detect without a full MIME library.
_SIGNATURES: list[_Sig] = [
    _Sig((b"%PDF-",), "pdf"),
    _Sig((b"\x89PNG\r\n\x1a\n",), "png"),
    _Sig((b"\xff\xd8\xff",), "jpeg"),
    _Sig((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"), "zip"),
    _Sig((b"\x1f\x8b",), "gz"),  # gzip
    _Sig((b"GIF87a", b"GIF89a"), "gif"),
    _Sig((b"OggS",), "ogg"),
    _Sig((b"fLaC",), "flac"),
    _Sig((b"ID3",), "mp3"),  # ID3 tag precedes MPEG frames
    _Sig((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), "mp3"),
    _Sig((b"BM",), "bmp"),
    _Sig((b"\x00\x00\x01\x00",), "ico"),
    _Sig((b"\x1aE\xdf\xa3",), "webm"),  # Matroska/WebM
    _Sig((b"ftyp",), "mp4"),  # ISO BMFF; at offset 4
    _Sig((b"{",), "json"),  # text heuristics; lenient
    _Sig((b"<",), "xml"),  # html/xml; lenient
]

# Extensions whose signature is too variable to sanity-check cheaply (e.g.
# LibreOffice text/document formats, fonts). These are allowed without a magic
# check. Formats with a strong, distinct magic signature (pdf/png/jpeg/gif/zip)
# are deliberately NOT here so extension-spoofing is caught.
_LENIENT_EXTENSIONS = {
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
    "rtf", "txt", "md", "html", "csv", "xml", "json", "epub", "mobi", "azw3",
    "ttf", "woff", "woff2", "otf", "eot",  # fonts
    "tar", "bz2", "xz", "m4a", "aac", "avi", "mkv",
}


def detect_type(head: bytes) -> str | None:
    """Return a best-effort type label from the leading bytes, or None."""
    # RIFF is a shared container header, so the form type at offset 8 is what
    # identifies the format. Without this a genuine ``RIFF…WEBP`` image was
    # reported as ``wav`` and rejected as a type mismatch on upload, which made
    # every WebP → anything conversion impossible.
    if len(head) >= 12 and head[:4] == b"RIFF":
        return "webp" if head[8:12] == b"WEBP" else "wav"
    for sig in _SIGNATURES:
        for prefix in sig.prefixes:
            if head.startswith(prefix):
                return sig.label
    # ISO BMFF (mp4/mov) has "ftyp" at offset 4.
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    return None


def validate_upload_signature(head: bytes, extension: str) -> bool:
    """Return True if ``head`` is consistent with ``extension``.

    Lenient: if the signature is unknown/ambiguous, or the extension is in the
    lenient allowlist, accept. Only a *clear* mismatch (e.g. a file claiming to
    be PDF but beginning with PNG magic) is rejected.
    """
    ext = (extension or "").lower().lstrip(".")
    if ext in _LENIENT_EXTENSIONS or not ext:
        return True

    detected = detect_type(head)
    if detected is None:
        # No recognizable signature (e.g. raw text) — accept; the converter
        # still applies its own validation and the zip-bomb/size guards.
        return True

    # Group "alias" extensions that share a signature.
    aliases = {
        "jpeg": {"jpg", "jpeg"},
        "jpg": {"jpg", "jpeg"},
        "mp4": {"mp4", "mov", "m4v"},
        "mov": {"mp4", "mov", "m4v"},
        "ogv": {"ogg", "ogv"},
        "webm": {"webm"},
        "wav": {"wav"},
        "webp": {"webp"},
        "mp3": {"mp3"},
        "zip": {"zip"},
        "gz": {"gz"},
    }
    accepted = aliases.get(detected, {detected})
    return ext in accepted
