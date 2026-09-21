"""Object-key sanitization for ISO 27001 A.8.24/8.25 (path traversal).

S3-compatible object keys are used as filesystem-like paths. An attacker who
can influence an object key (e.g. via an upload ``file_extension`` or a
``file_name``) must not be able to escape the intended bucket prefix with
``..`` or absolute-path sequences. This module centralises that guard.
"""

import re
from pathlib import PurePosixPath

# Reject anything that could escape the key namespace / perform traversal.
_FORBIDDEN = re.compile(r"(^|/)\.\.(/|$)|(^/)|[\\]|(\x00)")
_MAX_KEY_LENGTH = 1024

# ``user_files.file_extension`` is VARCHAR(20); clamp to fit without erroring.
_MAX_EXTENSION_LENGTH = 20

# Compound archive extensions the product recognises. This is extension
# *parsing*, not a format/category taxonomy: the frontend owns the
# extension -> category mapping and knows these keys (see
# ``web/src/lib/formatVisual.ts``).
_COMPOUND_EXTENSIONS = frozenset({"tar.gz", "tar.bz2", "tar.xz"})


def normalize_extension(extension: str | None) -> str:
    """Normalise a client-supplied extension to lowercase, dot-free form.

    ``".PDF"`` -> ``"pdf"``; ``None`` / ``""`` -> ``""``. The result is
    clamped to the ``user_files.file_extension`` column width.
    """
    return (extension or "").strip().lstrip(".").lower()[:_MAX_EXTENSION_LENGTH]


def extension_from_filename(file_name: str | None) -> str:
    """Derive a normalised extension from a file name.

    Fallback for when the upload session does not carry an explicit
    extension (e.g. legacy Redis sessions). Rules:

    * empty name / no dot (``"README"``) -> ``""``
    * trailing dot (``"report."``) -> ``""``
    * leading-dot dotfile (``".gitignore"``) -> ``""``
    * plain extension (``"report.pdf"``) -> ``"pdf"``
    * known compound archive (``"archive.tar.bz2"``) -> ``"tar.bz2"``

    The value is lowercased, keeps no leading dot, and is clamped to the
    ``user_files.file_extension`` column width.
    """
    name = PurePosixPath((file_name or "").replace("\\", "/")).name
    if not name or name.startswith("."):
        return ""
    _, dot, tail = name.rpartition(".")
    if not dot or not tail:
        return ""
    ext = tail.lower()
    for compound in _COMPOUND_EXTENSIONS:
        if name.lower().endswith("." + compound):
            return compound
    return ext[:_MAX_EXTENSION_LENGTH]


class UnsafeObjectKeyError(ValueError):
    """Raised when a candidate object key is unsafe for object storage."""


def sanitize_object_key(key: str, *, allow_slashes: bool = True) -> str:
    """Validate and normalise an object key.

    Args:
        key: The candidate object key.
        allow_slashes: Whether nested (slash-separated) keys are permitted.
            Upload-generated keys use slashes; user-supplied file names are
            stripped to a single leaf segment.

    Returns:
        The sanitised key.

    Raises:
        UnsafeObjectKeyError: If the key contains traversal, is absolute, or is
            too long / empty.
    """
    if not key or len(key) > _MAX_KEY_LENGTH:
        raise UnsafeObjectKeyError("Object key is empty or too long.")
    if _FORBIDDEN.search(key):
        raise UnsafeObjectKeyError("Object key contains forbidden path sequences.")
    if not allow_slashes and "/" in key:
        raise UnsafeObjectKeyError("Object key must be a single path segment.")

    # Collapse any redundant separators and reject resulting empty segments
    # (e.g. "a//b") that some storage backends interpret oddly.
    cleaned = PurePosixPath(key).as_posix()
    if cleaned != key or cleaned.startswith("/") or cleaned.endswith("/"):
        raise UnsafeObjectKeyError("Object key is not a clean relative path.")
    return cleaned


def sanitize_filename(name: str) -> str:
    """Reduce a user-supplied file name to a safe single leaf segment."""
    # Take only the final path component; strip traversal and separators.
    leaf = PurePosixPath(name.replace("\\", "/")).name
    if not leaf or leaf in (".", ".."):
        raise UnsafeObjectKeyError("File name is unsafe.")
    if len(leaf) > 255:
        leaf = leaf[-255:]
    return leaf
