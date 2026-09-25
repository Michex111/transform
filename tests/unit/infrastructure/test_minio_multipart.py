"""Tests for the MinIO/S3 multipart adapter.

These run fully offline. Presigning is local computation, but the SDK normally
looks the bucket's region up over the network first, so the client is built with
an explicit ``region`` — that is what makes the presigned-URL assertion possible
without a bucket. The complete/abort paths are exercised against recording
stand-ins for the SDK's multipart helpers, which is what lets us pin the argument
mapping (especially the ETag quoting rule) without a provider.
"""

import asyncio

import pytest
from minio import Minio
from minio.datatypes import Part
from minio.error import S3Error

from src.infrastructure.adapters.storage.exceptions import StorageOperationError
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioUrlStorageAdapter

BUCKET = "transform-convertion-bucket"
ENDPOINT = "minio:9000"


def _client() -> Minio:
    # ``region`` is pinned so presigning never performs a GetBucketLocation call
    # (which would need a reachable endpoint).
    return Minio(
        ENDPOINT,
        access_key="access-key",
        secret_key="secret-key",
        secure=False,
        region="us-east-1",
    )


@pytest.fixture
def adapter() -> MinioUrlStorageAdapter:
    return MinioUrlStorageAdapter(_client(), BUCKET, 15)


def test_generate_part_upload_url_signs_the_part_parameters(adapter) -> None:
    """The part number and upload id must be part of the SIGNED query string.

    That is what makes a part URL usable for exactly one part of one upload: if
    the parameters were appended after signing, the signature would be invalid;
    if they were outside the signature, a URL could be replayed elsewhere.
    """
    url = adapter.generate_part_upload_url("upload/x.bin", 7, "up-123", 120)

    assert "partNumber=7" in url
    assert "uploadId=up-123" in url
    assert "X-Amz-Signature=" in url
    # 120 minutes, not the adapter's default 15.
    assert "X-Amz-Expires=7200" in url


def test_generate_part_upload_url_does_not_leak_the_secret(adapter) -> None:
    url = adapter.generate_part_upload_url("upload/x.bin", 1, "up-1", 120)
    assert "secret-key" not in url


def test_complete_multipart_upload_strips_etag_quotes(adapter) -> None:
    """The SDK re-adds the quotes when it writes the request XML. Passing a
    quoted ETag through would send ``""etag""`` and the provider would reject
    the completion — after the whole transfer."""
    captured: dict = {}

    def fake_complete(bucket_name, object_name, upload_id, parts, ssec=None):
        captured.update(
            bucket=bucket_name, key=object_name, upload_id=upload_id, parts=parts,
        )

    adapter._minio_client._complete_multipart_upload = fake_complete  # type: ignore[method-assign]

    asyncio.run(adapter.complete_multipart_upload("upload/x.bin", "up-1", [(1, '"abc"'), (2, 'def')]))

    assert captured["key"] == "upload/x.bin"
    assert captured["upload_id"] == "up-1"
    assert captured["parts"] == [Part(part_number=1, etag="abc"), Part(part_number=2, etag="def")]


def test_complete_multipart_upload_wraps_provider_errors(adapter) -> None:
    def boom(*args, **kwargs):
        raise S3Error(None, "NoSuchUpload", "gone", "res", "req", "host")

    adapter._minio_client._complete_multipart_upload = boom  # type: ignore[method-assign]

    with pytest.raises(StorageOperationError):
        asyncio.run(adapter.complete_multipart_upload("upload/x.bin", "up-1", [(1, "abc")]))


def test_abort_tolerates_a_missing_upload(adapter) -> None:
    """Abort runs on cleanup paths: a provider that already forgot the upload
    must not turn a delete into an error."""

    def gone(*args, **kwargs):
        raise S3Error(None, "NoSuchUpload", "gone", "res", "req", "host")

    adapter._minio_client._abort_multipart_upload = gone  # type: ignore[method-assign]

    asyncio.run(adapter.abort_multipart_upload("upload/x.bin", "up-1"))


def test_abort_wraps_unexpected_provider_errors(adapter) -> None:
    def boom(*args, **kwargs):
        raise S3Error(None, "AccessDenied", "denied", "res", "req", "host")

    adapter._minio_client._abort_multipart_upload = boom  # type: ignore[method-assign]

    with pytest.raises(StorageOperationError):
        asyncio.run(adapter.abort_multipart_upload("upload/x.bin", "up-1"))


def test_create_multipart_upload_returns_the_provider_id(adapter) -> None:
    captured: dict = {}

    def fake_create(bucket_name, object_name, headers):
        captured.update(bucket=bucket_name, key=object_name, headers=dict(headers))
        return "provider-id"

    adapter._minio_client._create_multipart_upload = fake_create  # type: ignore[method-assign]

    assert adapter.create_multipart_upload("upload/x.bin") == "provider-id"
    assert captured["bucket"] == BUCKET
    assert captured["key"] == "upload/x.bin"
    # The SDK mutates the header dict, so it must never be a shared literal.
    assert captured["headers"]["Content-Type"] == "application/octet-stream"
