"""Safe construction of response headers that embed user-controlled values.

WHY this exists: several download endpoints put a file's display name into a
``Content-Disposition`` header, and that name is ultimately user-supplied (an
upload name, or a rename). Interpolating it verbatim lets a crafted name break
out of the quoted string (a stray ``"``) or inject a header (a CR/LF), which is
a response-splitting / header-injection weakness. The value must be encoded, not
trusted — and doing it in one place means a new download endpoint cannot forget
to.
"""

from urllib.parse import quote

#: Fallback when a name reduces to nothing usable (e.g. only control chars).
_FALLBACK_NAME = "download"


def content_disposition_attachment(filename: str) -> str:
    """Build a safe ``Content-Disposition: attachment`` header value.

    The name is emitted twice, as RFC 6266 prescribes: an ASCII ``filename=``
    for clients that ignore the extended form, and ``filename*=UTF-8''…``
    percent-encoded for everything modern. Both are derived from a copy with the
    characters that could escape the quoted string — ``"``, ``\\`` and CR/LF —
    plus all other control characters removed, so no combination of quotes and
    newlines can terminate the header early.
    """
    cleaned = "".join(
        ch for ch in filename if ch.isprintable() and ch not in '";\\'
    ).strip() or _FALLBACK_NAME
    ascii_name = cleaned.encode("ascii", "ignore").decode("ascii") or _FALLBACK_NAME
    encoded_name = quote(cleaned, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
