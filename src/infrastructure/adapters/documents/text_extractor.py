"""Best-effort text extraction for the documents the assistant can read.

WHY hand-rolled parsing instead of a library per format: the assistant needs
*text*, not fidelity. Every supported format here is either plain bytes or a ZIP
of XML, so ``zipfile`` + ``ElementTree`` covers Word, Excel and PowerPoint with
no new dependency — and ``pypdfium2`` is already installed for PDF→image
conversions, so PDFs come for free too.

Everything is bounded and nothing raises. A ten-year-old malformed ``.docx``
must produce a sentence the model can relay ("I could not read that"), not a
500 in the middle of a chat turn, and must never make the server read a
gigabyte into memory to find out.

Security notes:

* ``AI_MAX_DOCUMENT_BYTES`` is checked before parsing, and the caller reads at
  most that many bytes, so a hostile ``stat_object`` cannot make us buffer more.
* XML with a ``DOCTYPE`` is refused. ``ElementTree`` does not resolve external
  entities (no XXE), but an internal entity-expansion bomb is still reachable
  and costs nothing to reject — the assistant never needs DTDs.
"""

import io
import zipfile
from xml.etree import ElementTree

from src.application.ports.document_text_port import ExtractedDocument
from src.infrastructure.adapters.storage.sanitize import extension_from_filename

#: Formats read as plain text. Everything here is human-readable already, so
#: "extraction" is decoding plus whitespace cleanup.
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "txt", "md", "markdown", "csv", "tsv", "json", "xml", "html", "htm",
        "yaml", "yml", "log", "srt", "vtt", "ini", "cfg", "py", "js", "ts", "sql",
    }
)

#: Shown alongside an unsupported-format rejection, so the model can tell the
#: user what *would* work instead of just refusing.
_SUPPORTED_HINT = "readable formats are plain text, PDF, .docx, .xlsx and .pptx"

#: How far an OOXML ZIP member may be allowed to expand, relative to the
#: compressed byte budget the caller enforced before parsing. This is a
#: decompression-bomb guard: ``ZipFile.read`` materialises the *whole* member in
#: memory, so a 10 MB "document" whose ``word/document.xml`` declares a
#: multi-gigabyte ``file_size`` would otherwise be a multi-gigabyte allocation —
#: exactly the "never read a gigabyte into memory" promise in the module
#: docstring. Honest OOXML parts are wordy XML that compress roughly 10x, so 16x
#: clears real documents while staying far below the ~1000x a DEFLATE bomb
#: reaches. The declared ``file_size`` is trustworthy as a bound because
#: ``zipfile`` reads at most that many decompressed bytes.
_MAX_ZIP_EXPANSION_RATIO = 16


def _local_name(tag: object) -> str:
    """Local name of an XML tag, with the namespace stripped.

    Matches by local name rather than by full ``{namespace}name`` so a document
    written by a different Office version (a different namespace URI) still
    parses.
    """
    if not isinstance(tag, str):
        # Comments and processing instructions have callable tags.
        return ""
    return tag.rsplit("}", 1)[-1]


def _safe_parse(data: bytes) -> ElementTree.Element:
    """Parse XML, refusing a DTD (see the module docstring)."""
    if b"<!doctype" in data[:4096].lower():
        raise ValueError("XML with a DTD is not supported")
    return ElementTree.fromstring(data)


def _xml_text(data: bytes, *, text_tags: frozenset[str], break_tags: frozenset[str]) -> str:
    """Concatenate the text-bearing elements of an OOXML part."""
    root = _safe_parse(data)
    parts: list[str] = []
    for element in root.iter():
        name = _local_name(element.tag)
        if name in break_tags:
            parts.append("\n")
        elif name in text_tags and element.text:
            parts.append(element.text)
    return "".join(parts)


def _decode_text(data: bytes) -> str:
    """Decode bytes as text, trying the encodings that actually appear."""
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    # ``latin-1`` cannot fail, so this is unreachable; kept for type totality.
    return data.decode("utf-8", errors="replace")


def _collapse_whitespace(text: str) -> str:
    """Normalise line endings and collapse runs of blank lines.

    Collapsing matters for the prompt: an OOXML paragraph per line already
    doubles the size of most documents, and the budget is spent on characters
    the model can use, not on layout.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in normalised.split("\n")]
    kept: list[str] = []
    for line in lines:
        if not line and kept and not kept[-1]:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


class DocumentTextExtractor:
    """Extracts text from the document formats the assistant supports."""

    def __init__(self, *, max_document_bytes: int, max_input_chars: int) -> None:
        self._max_document_bytes = max_document_bytes
        self._max_input_chars = max_input_chars

    @property
    def _member_cap(self) -> int:
        """Largest uncompressed ZIP member this extractor will materialise.

        Allows a generous expansion over the compressed budget (see
        :data:`_MAX_ZIP_EXPANSION_RATIO`) so a genuine document is never
        rejected, while making a decompression bomb fail closed.
        """
        return max(1, self._max_document_bytes) * _MAX_ZIP_EXPANSION_RATIO

    def extract(self, *, file_name: str, data: bytes) -> ExtractedDocument:
        """Extract text from ``data``; never raises (see the module docstring)."""
        extension = extension_from_filename(file_name)

        if len(data) > self._max_document_bytes:
            return ExtractedDocument(
                text="",
                truncated=False,
                supported=False,
                note=(
                    f"the file is larger than the "
                    f"{self._max_document_bytes // (1024 * 1024)} MB limit for reading"
                ),
            )

        if not data:
            return ExtractedDocument(text="", truncated=False, supported=True, note="")

        try:
            raw = self._extract_bytes(extension, data)
        except _UnsupportedFormat:
            return ExtractedDocument(
                text="",
                truncated=False,
                supported=False,
                note=(
                    f"'.{extension}' is not a format I can read ({_SUPPORTED_HINT})"
                    if extension
                    else "I cannot tell what format this file is"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — a parse failure is a value here
            return ExtractedDocument(
                text="",
                truncated=False,
                supported=False,
                note=f"the file could not be parsed ({type(exc).__name__})",
            )

        text = _collapse_whitespace(raw)
        truncated = len(text) > self._max_input_chars
        return ExtractedDocument(
            text=text[: self._max_input_chars],
            truncated=truncated,
            supported=True,
        )

    def _extract_bytes(self, extension: str, data: bytes) -> str:
        if extension in _TEXT_EXTENSIONS:
            return _decode_text(data)
        if extension == "pdf":
            return self._extract_pdf(data)
        if extension == "docx":
            return self._extract_docx(data)
        if extension == "xlsx":
            return self._extract_xlsx(data)
        if extension == "pptx":
            return self._extract_pptx(data)
        raise _UnsupportedFormat(extension)

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------

    def _extract_pdf(self, data: bytes) -> str:
        """Per-page text, stopping as soon as the character budget is spent.

        Stopping early is what keeps a 500-page PDF cheap: there is no reason to
        walk pages whose text would be discarded.
        """
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(data)
        try:
            pages: list[str] = []
            total = 0
            for page in document:
                textpage = page.get_textpage()
                try:
                    page_text = textpage.get_text_range()
                finally:
                    textpage.close()
                    page.close()
                pages.append(page_text)
                total += len(page_text)
                if total > self._max_input_chars:
                    break
            return "\n".join(pages)
        finally:
            document.close()

    # ------------------------------------------------------------------
    # OOXML
    # ------------------------------------------------------------------

    def _extract_docx(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = _read_member(archive, "word/document.xml", cap=self._member_cap)
        return _xml_text(xml, text_tags=frozenset({"t"}), break_tags=frozenset({"p"}))

    def _extract_pptx(self, data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            slides = sorted(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            # A deck can hold thousands of slides; cap the TOTAL decompressed
            # bytes as well as each member so many small-but-honest parts cannot
            # add up to the same bomb.
            cap = self._member_cap
            parts: list[str] = []
            total = 0
            for name in slides:
                member = _read_member(archive, name, cap=cap - total)
                total += len(member)
                parts.append(
                    _xml_text(member, text_tags=frozenset({"t"}), break_tags=frozenset({"p"}))
                )
        return "\n".join(parts)

    def _extract_xlsx(self, data: bytes) -> str:
        """Tab-separated rows, resolving the shared-string indirection.

        Excel stores most text once in ``xl/sharedStrings.xml`` and refers to it
        by index from each cell, so reading the worksheets alone yields numbers
        and indices rather than content.
        """
        cap = self._member_cap
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared = self._xlsx_shared_strings(archive, cap=cap)
            sheets = sorted(
                name
                for name in archive.namelist()
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            )
            lines: list[str] = []
            total = 0
            for name in sheets:
                member = _read_member(archive, name, cap=cap - total)
                total += len(member)
                root = _safe_parse(member)
                for row in root.iter():
                    if _local_name(row.tag) != "row":
                        continue
                    cells = [
                        _xlsx_cell_text(cell, shared)
                        for cell in row
                        if _local_name(cell.tag) == "c"
                    ]
                    if any(cells):
                        lines.append("\t".join(cells))
        return "\n".join(lines)

    @staticmethod
    def _xlsx_shared_strings(archive: zipfile.ZipFile, *, cap: int) -> list[str]:
        try:
            xml = _read_member(archive, "xl/sharedStrings.xml", cap=cap)
        except (KeyError, ValueError):
            # Absent part (a sheet with only inline strings) is normal; a part
            # over the cap is treated the same way rather than aborting the
            # whole read, since the worksheets alone are still summarisable.
            return []
        root = _safe_parse(xml)
        strings: list[str] = []
        for element in root.iter():
            if _local_name(element.tag) != "si":
                continue
            strings.append(
                "".join(
                    child.text or ""
                    for child in element.iter()
                    if _local_name(child.tag) == "t"
                )
            )
        return strings


class _UnsupportedFormat(Exception):
    """Internal signal: this extension has no extractor."""


def _read_member(archive: zipfile.ZipFile, name: str, *, cap: int) -> bytes:
    """Read a ZIP member, bounded by ``cap`` uncompressed bytes.

    The member's declared ``file_size`` is checked *before* the read, so a zip
    bomb is refused without ever decompressing it. ``zipfile`` itself reads at
    most the declared size, so the declaration is a trustworthy upper bound on
    what the allocation will be.
    """
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ValueError(f"missing {name}") from exc
    if info.file_size > cap:
        raise ValueError(
            f"{name} expands to {info.file_size} bytes, over the reading limit"
        )
    return archive.read(name)



def _xlsx_cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    """Text of one ``<c>`` cell, resolving shared strings and inline strings."""
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(
            child.text or ""
            for child in cell.iter()
            if _local_name(child.tag) == "t"
        )

    value = ""
    for child in cell:
        if _local_name(child.tag) == "v":
            value = child.text or ""
            break
    if cell_type == "s" and value:
        try:
            index = int(value)
        except ValueError:
            return value
        if 0 <= index < len(shared):
            return shared[index]
        return ""
    return value
