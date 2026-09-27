"""Extension → MIME type, for the content types we can state honestly.

WHY this exists instead of ``mimetypes.guess_type``: the standard library's
table is incomplete for exactly the formats this product advertises, and it is
inconsistent between platforms (on Linux it is populated from
``/etc/mime.types``, which may or may not be installed). Measured on the
installed runtime: ``guess_type("probe.opus")`` → ``None``,
``guess_type("probe.oga")`` → ``None``, ``guess_type("probe.m4a")`` →
``("audio/x-m4a", None)``. A ``None`` falls back to
``application/octet-stream`` and an ``audio/x-*`` type is a legacy alias, so a
file's own name could not be turned into a correct type.

WHY the database holds a derived type rather than the object store's: the
stored ``Content-Type`` is whatever the *upload* left behind, and the single-PUT
path deliberately signs its presigned URL without a ``Content-Type`` (sending
one breaks the signature — see ``putToPresignedUrl``, which sends the bytes with
no header to avoid a ``SignatureDoesNotMatch``). So the provider applies its own
default and the API was recording that as the file's type. Measured against the
live backblaze bucket: every single-PUT object came back as
``application/x-www-form-urlencoded`` and every multipart object as
``application/octet-stream`` — so the same song was described differently
depending on whether it was over the 100 MiB multipart threshold, and neither
value described a song.

The file NAME is the one signal that survives the whole pipeline, which is why
the type is derived from the extension here. ``previewMimeType`` in
``web/src/lib/filePreview.ts`` is the same idea for the browser side; the two
tables are intentionally independent (they serve different layers and are
tested separately) but must agree on the formats the product supports.
"""

from __future__ import annotations

#: Content type used when nothing better is known.
FALLBACK_MIME_TYPE = "application/octet-stream"

#: Values that mean "the uploader did not say", never a real file type.
#:
#: ``application/x-www-form-urlencoded`` is what the object store reports for an
#: untyped single PUT (see the module docstring). It is in this set so a legacy
#: row carrying it is repaired by ``mime_type_for`` rather than echoed back as
#: though a form submission were a video.
GENERIC_MIME_TYPES = frozenset(
    {
        "",
        FALLBACK_MIME_TYPE,
        "binary/octet-stream",
        "application/x-www-form-urlencoded",
        "application/unknown",
        "application/binary",
        "unknown/unknown",
    }
)

_EXTENSION_MIME_TYPES: dict[str, str] = {
    # documents
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "xml": "application/xml",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "log": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "css": "text/css",
    # spreadsheets / presentations
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odp": "application/vnd.oasis.opendocument.presentation",
    # images
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "avif": "image/avif",
    "heic": "image/heic",
    "heif": "image/heic",
    "ico": "image/x-icon",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    # video
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "ogv": "video/ogg",
    "flv": "video/x-flv",
    "wmv": "video/x-ms-wmv",
    "mpg": "video/mpeg",
    "mpeg": "video/mpeg",
    "3gp": "video/3gpp",
    # audio
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    # Ogg Opus shares the Ogg container, so `audio/ogg` is what a browser is
    # given for it; the bare `audio/opus` labels exist but are not what `<audio>`
    # playback expects.
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    # ALAC and the M4B audiobook are MP4 containers, like M4A.
    "alac": "audio/mp4",
    "m4b": "audio/mp4",
    "m4r": "audio/mp4",
    "aac": "audio/aac",
    "wma": "audio/x-ms-wma",
    "aiff": "audio/aiff",
    "mid": "audio/midi",
    "midi": "audio/midi",
    # archives / ebooks
    "zip": "application/zip",
    "gz": "application/gzip",
    "tar": "application/x-tar",
    "bz2": "application/x-bzip2",
    "xz": "application/x-xz",
    "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar",
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
}


def mime_type_for(extension: str) -> str | None:
    """The MIME type for an extension, or ``None`` when we do not know one.

    Accepts the extension with or without a leading dot and in any case.

    A **compound** extension resolves on its last segment, because
    ``extension_from_filename`` deliberately keeps recognised archives compound
    (``archive.tar.gz`` → ``tar.gz``). ``tar.gz`` is not a registered type, but
    the outer layer it names — gzip — is, so the correct answer is
    ``application/gzip`` rather than nothing. A caller can still tell that the
    answer describes the outer container, which is what a download or a preview
    needs anyway.
    """
    key = (extension or "").strip().lower().lstrip(".")
    if not key:
        return None
    found = _EXTENSION_MIME_TYPES.get(key)
    if found is not None:
        return found
    outer = key.rpartition(".")[2]
    return _EXTENSION_MIME_TYPES.get(outer) if outer and outer != key else None
