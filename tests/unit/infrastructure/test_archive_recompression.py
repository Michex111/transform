"""Tests for single-file archive recompression (W-20).

``_simple_recompress`` used to do ``data = fh.read()`` and hold the compressed
*and* the decompressed payload in RAM at once. The worker container has no
memory limit, so a large (or adversarially compressed) file could exhaust the
process. The fix streams through ``shutil.copyfileobj``.
"""

import bz2
import gzip
import lzma
import shutil

import pytest

# Importing the module registers every archive conversion in the global
# registry used below.
import src.infrastructure.converters.functions.archive.archive_converters as archive_module  # noqa: F401
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry


def _compress(payload: bytes, fmt: str) -> bytes:
    if fmt == "gz":
        return gzip.compress(payload)
    if fmt == "bz2":
        return bz2.compress(payload)
    return lzma.compress(payload)


def _decompress(payload: bytes, fmt: str) -> bytes:
    if fmt == "gz":
        return gzip.decompress(payload)
    if fmt == "bz2":
        return bz2.decompress(payload)
    return lzma.decompress(payload)


@pytest.mark.parametrize(
    ("source", "target"),
    [("gz", "bz2"), ("bz2", "xz"), ("xz", "gz"), ("gz", "lzma"), ("lzma", "bz2")],
)
def test_recompression_round_trips_the_content(source: str, target: str, tmp_path) -> None:
    payload = (b"streamed-payload-" * 5000) + bytes(range(256))
    source_file = tmp_path / f"input.{source}"
    source_file.write_bytes(_compress(payload, source))
    output_file = tmp_path / f"output.{target}"

    converter = converter_registry.get_converter(ConversionType(source, target))
    assert converter is not None, f"no converter registered for {source}->{target}"
    converter(str(source_file), str(output_file))

    assert _decompress(output_file.read_bytes(), target) == payload


def test_recompression_streams_instead_of_buffering(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The payload must be piped, not materialised in a single ``read()``."""
    calls: list[tuple] = []
    real_copyfileobj = shutil.copyfileobj

    def spy(src, dst, *args, **kwargs):
        calls.append(args)
        return real_copyfileobj(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfileobj", spy)

    payload = b"bounded-memory" * 1000
    source_file = tmp_path / "input.gz"
    source_file.write_bytes(gzip.compress(payload))
    output_file = tmp_path / "output.bz2"

    converter = converter_registry.get_converter(ConversionType("gz", "bz2"))
    assert converter is not None
    converter(str(source_file), str(output_file))

    # A single streaming copy, called with the default (non-zero) buffer size.
    assert calls == [()]
    assert _decompress(output_file.read_bytes(), "bz2") == payload
