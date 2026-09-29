"""In-memory document builders for tests.

Several suites need a real ``.docx``/``.pdf``/… to feed an extractor or a
converter, and there is nothing in the runtime dependencies that can author one.
Keeping the builders here means the byte layouts (and, for the PDF, the xref
offsets) are written once and shared, instead of each test growing its own
slightly-different fixture.
"""

import io
import zipfile

DOCX_DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Quarterly report</w:t></w:r></w:p>
    <w:p><w:r><w:t>Revenue is up 12%.</w:t></w:r></w:p>
  </w:body>
</w:document>"""

PPTX_SLIDE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <a:p><a:r><a:t>Welcome to the deck</a:t></a:r></a:p>
    <a:p><a:r><a:t>Second bullet</a:t></a:r></a:p>
  </p:spTree></p:cSld>
</p:sld>"""

XLSX_SHARED_STRINGS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>Name</t></si>
  <si><t>Widget</t></si>
</sst>"""

XLSX_SHEET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
    <row><c><v>42</v></c></row>
  </sheetData>
</worksheet>"""


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF whose only content is one line of text.

    Built byte by byte, including real xref offsets, so a caller exercises the
    actual PDFium code path. PDFs are otherwise impossible to author from the
    runtime dependencies, and a checked-in binary would hide what is asserted.
    """
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    total = len(objects) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def docx_bytes(document_xml: bytes = DOCX_DOCUMENT_XML) -> bytes:
    """A ``.docx`` containing only ``word/document.xml``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def pptx_bytes() -> bytes:
    """A ``.pptx`` with a single slide."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", PPTX_SLIDE_XML)
    return buffer.getvalue()


def xlsx_bytes() -> bytes:
    """A ``.xlsx`` with one sheet and a shared-string table."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", XLSX_SHARED_STRINGS_XML)
        archive.writestr("xl/worksheets/sheet1.xml", XLSX_SHEET_XML)
    return buffer.getvalue()


def zip_bytes() -> bytes:
    """A minimal, unsupported container for the extractor's rejection path."""
    return b"PK\x03\x04not really a zip"
