"""Turn a document into readable text for an MCP agent.

WHY this exists: ``download_file`` returns raw bytes as base64. A language model
cannot reliably decode base64 in its head, and for PDF, DOCX, PPTX or XLSX the
bytes are a container format rather than text at all. Without this service an
agent's only way to *read* a document was convert → poll → save to the Drive →
download (base64 again), which polluted the user's Drive and mostly produced
garbage.

Two paths, chosen by the source extension:

* **Text-ish formats** (``txt``, ``md``, ``csv``, ``json``, source code, …) are
  decoded directly, with a charset fallback so a Windows-1252 export is not
  reported as corrupt.
* **Container formats** (``pdf``, ``docx``, ``odt``, ``rtf``, ``pptx``, ``xlsx``,
  ``epub``, ``html``) are extracted through the *same* converter functions the
  workers use, targeting ``txt`` (preferred) or ``md``. This happens in-process
  and creates no job and spends no credits: reading a document is not a
  conversion the user asked for, and charging for it or cluttering their history
  with it would be wrong.

Extraction results are cached per ``(extension, sha256(content))`` so a
paginated read re-decodes a PDF once rather than once per page. The cache is
bounded — an unbounded one is a memory leak keyed by user content.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.storage.sanitize import normalize_extension
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.infrastructure.converters.converter_registry import get_registry

#: Extensions that are text by nature and need no conversion.
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "txt", "text", "md", "markdown", "csv", "tsv", "json", "jsonl", "xml",
        "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "rst", "tex",
        "html", "htm", "css", "js", "ts", "tsx", "jsx", "py", "rb", "go", "rs",
        "java", "c", "h", "cpp", "hpp", "sh", "sql", "svg",
    }
)

#: Preferred extraction targets, most faithful first. ``md`` keeps headings and
#: tables that ``txt`` flattens; ``txt`` is the universal fallback.
_TARGET_PRIORITY: tuple[str, ...] = ("md", "txt")

#: Extensions decoded as UTF-16 when a UTF-8 decode fails and the bytes start
#: with a BOM. Everything else falls back to Latin-1, which never fails and is
#: the least-surprising last resort for an unknown 8-bit encoding.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

_MAX_CACHE_ENTRIES = 16


class TextExtractionUnavailable(Exception):
    """The document's text could not be produced, with an actionable reason."""


def decode_text(data: bytes) -> str:
    """Decode text bytes, preferring an explicit BOM and never raising."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                break
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Latin-1 maps every byte, so this always succeeds. It is used only for
        # content that is not valid UTF-8; refusing it would make an ordinary
        # Windows-1252 CSV unreadable for no security benefit.
        return data.decode("latin-1")


class MCPTextExtractor:
    """Best-effort, cached text extraction for agent reads."""

    def __init__(self, conversion_map: dict[str, list[str]] | None = None) -> None:
        self._map = conversion_map or build_conversion_map()
        self._cache: OrderedDict[str, str] = OrderedDict()

    def can_extract(self, extension: str | None) -> bool:
        ext = normalize_extension(extension or "")
        if ext in TEXT_EXTENSIONS:
            return True
        return any(target in self._map.get(ext, ()) for target in _TARGET_PRIORITY)

    async def extract(self, extension: str | None, file_name: str, data: bytes) -> str:
        """Return the document's text, converting a container format if needed."""
        ext = normalize_extension(extension or "")
        if ext in TEXT_EXTENSIONS:
            return decode_text(data)

        target = next(
            (t for t in _TARGET_PRIORITY if t in self._map.get(ext, ())), None
        )
        if target is None:
            raise TextExtractionUnavailable(
                f"{ext or 'That'} files cannot be read as text by this server. "
                "Convert it to pdf, txt or md first, then read the result."
            )

        key = hashlib.sha256(f"{ext}:{target}:".encode() + data).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        text = await asyncio.to_thread(self._convert, ext, target, file_name, data)
        self._cache[key] = text
        self._cache.move_to_end(key)
        while len(self._cache) > _MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return text

    @staticmethod
    def _convert(ext: str, target: str, file_name: str, data: bytes) -> str:
        converter = get_registry().get_converter(
            ConversionType(source_format=ext, target_format=target)
        )
        if converter is None:
            raise TextExtractionUnavailable(
                f"There is no text extractor for {ext} files in this deployment."
            )
        with tempfile.TemporaryDirectory(prefix="mcp-read-") as workdir:
            stem = Path(file_name or f"document.{ext}").stem or "document"
            source = Path(workdir) / f"{stem}.{ext}"
            destination = Path(workdir) / f"{stem}.{target}"
            source.write_bytes(data)
            try:
                converter(str(source), str(destination))
            except FileNotFoundError as exc:
                # The converter shells out (LibreOffice, pdftotext, …) and the
                # binary is absent from this image. Say so plainly instead of
                # surfacing a stack trace to the model.
                raise TextExtractionUnavailable(
                    "Text extraction is not available in this deployment: a required "
                    "converter tool is not installed."
                ) from exc
            except Exception as exc:  # noqa: BLE001 - converter failures vary
                raise TextExtractionUnavailable(
                    "That document's text could not be extracted."
                ) from exc
            if not destination.exists() or destination.stat().st_size == 0:
                raise TextExtractionUnavailable(
                    "That document produced no extractable text. It may be a scanned "
                    "image without an OCR layer."
                )
            return decode_text(destination.read_bytes())


def page_text(text: str, offset: Any, max_chars: Any, default_chars: int) -> dict[str, Any]:
    """Slice ``text`` into one character page with paging metadata.

    Character-based (not byte-based) on purpose: an agent reasons about text, a
    mid-character split would produce a replacement glyph, and every format the
    document came from has already lost its original byte boundaries.
    """
    total = len(text)
    start = 0
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    limit = default_chars
    try:
        requested = int(max_chars) if max_chars is not None else default_chars
        limit = max(1, min(requested, default_chars))
    except (TypeError, ValueError):
        limit = default_chars
    if start >= total and total > 0:
        return {
            "ok": False,
            "error": f"offset {start} is past the end of this document, which is {total} characters.",
            "total_chars": total,
        }
    end = min(total, start + limit)
    return {
        "ok": True,
        "content": text[start:end],
        "offset": start,
        "chars_returned": end - start,
        "total_chars": total,
        "has_more": end < total,
        **({"next_offset": end} if end < total else {}),
    }
