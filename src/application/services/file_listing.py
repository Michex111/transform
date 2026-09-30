"""Ordering vocabulary for a file listing, shared by every layer that needs it.

The assistant gets asked for a *subset* of a drive rather than the whole of it —
"what's my largest file?", "which of my files are the largest?" — and answering
that honestly means the **database** has to order the rows, not the model. A
tool that fetched a page and then sorted it in Python would answer from an
arbitrary page: with the default page size, the "largest file" would only ever
be the largest file among the first twenty rows, and the answer would be wrong
in exactly the case the user asked about.

This module is deliberately the only place the allowed keys and directions are
written down: the tool schema publishes them to the model, the toolbox validates
the model's untrusted strings against them, and the SQL repository maps them to
columns. Adding a sort key is therefore one entry here plus one column mapping —
it cannot drift into three half-updated copies.
"""

from enum import StrEnum


class FileSortKey(StrEnum):
    """A file attribute a listing may be ordered by."""

    NAME = "name"
    SIZE = "size"
    DATE = "date"


class FileSortOrder(StrEnum):
    """Which way a listing runs (``asc`` = smallest/oldest/A-first)."""

    ASC = "asc"
    DESC = "desc"


#: Newest first. This is what every listing did before sorting existed, so it is
#: the default for every caller that does not pass one, and adding the vocabulary
#: changed no existing response.
DEFAULT_FILE_SORT = FileSortKey.DATE
DEFAULT_FILE_SORT_ORDER = FileSortOrder.DESC


def parse_file_sort(
    value: object, *, default: FileSortKey = DEFAULT_FILE_SORT
) -> FileSortKey:
    """Coerce an untrusted ``sort`` argument into a :class:`FileSortKey`.

    The value arrives from a JSON blob the model wrote, so it is treated as a
    *hint*: anything unrecognised reads as ``default`` instead of raising, which
    keeps a typo in a tool call from ending the turn. The default is also the
    safe reading of a missing argument, because it leaves the ordering the caller
    already had.
    """
    if isinstance(value, str):
        try:
            return FileSortKey(value.strip().lower())
        except ValueError:
            return default
    return default


def parse_file_sort_order(
    value: object, *, default: FileSortOrder = DEFAULT_FILE_SORT_ORDER
) -> FileSortOrder:
    """Coerce an untrusted ``order`` argument into a :class:`FileSortOrder`."""
    if isinstance(value, str):
        try:
            return FileSortOrder(value.strip().lower())
        except ValueError:
            return default
    return default
