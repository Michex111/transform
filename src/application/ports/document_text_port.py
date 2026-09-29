"""Dependency-inversion port for turning an uploaded document into text.

WHY this exists: the assistant's most useful abilities (summarising a file,
deciding whether it is worth converting) all reduce to "read the text of this
document". That is an infrastructure concern — PDF and OOXML parsing, with
their own failure modes and limits — and it must be swappable for a fake in
tests. The port is synchronous on purpose: parsing is CPU-bound with no I/O, so
the *caller* decides whether to offload it to a thread.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractedDocument:
    """The result of a best-effort text extraction.

    ``supported`` distinguishes "this format is one we read" from "this file
    yielded no text": an unsupported format is a decision to report to the user
    (potentially with a suggestion to convert it first), whereas an empty text
    is just an empty document. Failures never raise — a corrupt upload must not
    turn an assistant turn into a 500, and the ``note`` is what the model/user
    is told instead.
    """

    text: str
    truncated: bool
    supported: bool
    note: str = ""


@runtime_checkable
class DocumentTextExtractorPort(Protocol):
    """Extracts plain text from a document's bytes."""

    def extract(self, *, file_name: str, data: bytes) -> ExtractedDocument:
        """Extract text from ``data``, using ``file_name`` only for the format.

        Must never raise: any parse failure is reported as ``supported=False``
        with a human-readable ``note``.
        """
        ...
