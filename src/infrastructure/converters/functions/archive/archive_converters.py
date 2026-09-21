"""Archive converters using the Python standard library.

Supports repacking between the archive formats in the UI catalog that can be
read/written with the standard library: zip, tar, tar.gz, tar.bz2, tar.xz.

Single-file compression formats (gz, bz2, xz, lzma) are supported for direct
recompression between one another.

Formats that need third-party tools (7z, rar, cab, iso, dmg, etc.) are not
registered here.
"""

import os
import tarfile
import zipfile
import tempfile
import logging

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

# True multi-file archives that can be extracted and repacked.
ARCHIVE_FORMATS = ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"]

# Single-file compression formats (recompression between each other).
COMPRESSION_FORMATS = ["gz", "bz2", "xz", "lzma"]

# Decompression-bomb guard: a tiny archive must not expand to an enormous,
# filesystem-exhausting payload. Cap the total uncompressed bytes and the
# number of entries.
MAX_ARCHIVE_EXPAND_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB total uncompressed
MAX_ARCHIVE_ENTRIES = 10_000


def _check_zip_bomb(zip_info: zipfile.ZipInfo) -> None:
    if zip_info.file_size > MAX_ARCHIVE_EXPAND_BYTES:
        raise RuntimeError("Archive member is too large after decompression (zip-bomb guard).")


def _extract(input_file: str, dest_dir: str) -> None:
    """Extract a source archive (or decompress a single file) into dest_dir.

    Guards against decompression bombs: totals are summed before extraction and
    entry counts are bounded so a tiny malicious archive cannot exhaust disk or
    CPU.
    """
    name = input_file.lower()
    if name.endswith(".zip") or zipfile.is_zipfile(input_file):
        with zipfile.ZipFile(input_file, "r") as zf:
            members = zf.infolist()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise RuntimeError("Archive has too many entries.")
            total = sum(m.file_size for m in members)
            if total > MAX_ARCHIVE_EXPAND_BYTES:
                raise RuntimeError("Archive expands beyond the permitted size (zip-bomb guard).")
            for member in members:
                _check_zip_bomb(member)
            zf.extractall(dest_dir)
    elif tarfile.is_tarfile(input_file):
        with tarfile.open(input_file, "r:*") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise RuntimeError("Archive has too many entries.")
            total = sum(m.size for m in members)
            if total > MAX_ARCHIVE_EXPAND_BYTES:
                raise RuntimeError("Archive expands beyond the permitted size (zip-bomb guard).")
            tf.extractall(dest_dir)
    elif name.endswith((".gz", ".bz2", ".xz", ".lzma")):
        # Single-file compression: decompress to a plain file in dest_dir.
        mode = "r:gz" if name.endswith(".gz") else "r:bz2" if name.endswith(".bz2") else "r:xz"
        with tarfile.open(input_file, mode) as tf:
            members = tf.getmembers()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise RuntimeError("Archive has too many entries.")
            total = sum(m.size for m in members)
            if total > MAX_ARCHIVE_EXPAND_BYTES:
                raise RuntimeError("Archive expands beyond the permitted size (zip-bomb guard).")
            tf.extractall(dest_dir)
    else:
        raise RuntimeError(f"Cannot extract archive type: {input_file}")


def _pack(src_dir: str, output_file: str, target: str) -> None:
    """Pack the contents of src_dir into an archive of the target format."""
    out_name = output_file.lower()
    if target == "zip" or out_name.endswith(".zip"):
        with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(src_dir):
                for f in files:
                    full_path = os.path.join(root, f)
                    arcname = os.path.relpath(full_path, src_dir)
                    zf.write(full_path, arcname=arcname)
        return

    mode = "w:gz" if target == "tar.gz" or out_name.endswith(".tar.gz") else (
        "w:bz2" if target == "tar.bz2" or out_name.endswith(".tar.bz2") else (
            "w:xz" if target == "tar.xz" or out_name.endswith(".tar.xz") else "w"
        )
    )
    with tarfile.open(output_file, mode) as tf:
        for root, _, files in os.walk(src_dir):
            for f in files:
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, src_dir)
                tf.add(full_path, arcname=arcname)


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        with tempfile.TemporaryDirectory() as tmpdir:
            _extract(input_file, tmpdir)
            _pack(tmpdir, output_file, target)

    converter.__name__ = f"archive_{source}_to_{target}"
    return converter


# Register repacking between true archives.
for _source in ARCHIVE_FORMATS:
    for _target in ARCHIVE_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )


def _make_recompress(source: str, target: str):
    """Convert a single-file compressed format (e.g. .gz -> .bz2)."""

    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _simple_recompress(input_file, output_file, source, target)

    converter.__name__ = f"archive_{source}_to_{target}"
    return converter


def _simple_recompress(input_file: str, output_file: str, source: str, target: str) -> None:
    """Decompress a single-file compressed format, then recompress to target.

    Streams through a fixed-size buffer rather than materialising the payload:
    ``fh.read()`` held the compressed *and* the uncompressed bytes in RAM at
    once, and the worker container has no memory limit, so a large (or
    adversarially compressed) file could exhaust the process.
    """
    import gzip
    import bz2
    import lzma
    import shutil

    openers = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open, "lzma": lzma.open}
    if source not in openers:
        raise RuntimeError(f"Unsupported compression source: {source}")
    if target not in openers:
        raise RuntimeError(f"Unsupported compression target: {target}")

    with openers[source](input_file, "rb") as src, openers[target](output_file, "wb") as out:
        shutil.copyfileobj(src, out)


for _source in COMPRESSION_FORMATS:
    for _target in COMPRESSION_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_recompress(_source, _target)
        )
