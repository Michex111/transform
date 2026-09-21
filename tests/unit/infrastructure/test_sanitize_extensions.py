"""Tests for the shared extension normalisation helpers."""

import pytest

from src.infrastructure.adapters.storage.sanitize import (
    extension_from_filename,
    normalize_extension,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PDF", "pdf"),
        (".pdf", "pdf"),
        ("..pdf", "pdf"),
        (" tar.bz2 ", "tar.bz2"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_extension(raw, expected) -> None:
    assert normalize_extension(raw) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "pdf"),
        ("REPORT.PDF", "pdf"),
        ("README", ""),               # no dot
        ("report.", ""),              # trailing dot
        (".gitignore", ""),           # dotfile / leading dot
        ("archive.tar.bz2", "tar.bz2"),  # multi-dot compound archive
        ("backup.tar.gz", "tar.gz"),
        ("bundle.tar.xz", "tar.xz"),
        ("my.resume.pdf", "pdf"),     # multi-dot, last segment
        ("dir/nested/file.PNG", "png"),  # path component ignored
        ("", ""),
        (None, ""),
    ],
)
def test_extension_from_filename(name, expected) -> None:
    assert extension_from_filename(name) == expected
