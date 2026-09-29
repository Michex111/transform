"""Request-schema bounds that keep unbounded client input out of the server.

WHY these are security tests and not validation nits: each bounded field is
forwarded into work whose cost is proportional to its size (base64 decoding, and
sorting/serialising a part list into the provider's CompleteMultipartUpload
XML), so an unbounded field is a cheap memory/CPU exhaustion vector on an
authenticated (or, for uploads, unauthenticated) endpoint.
"""

import pytest
from pydantic import ValidationError

from src.presentation.schemas.conversion import CreateConversionJobRequest
from src.presentation.schemas.upload import UploadPartEtag, UploadVerifyRequest


def test_data_key_is_length_bounded() -> None:
    """A 32-byte base64 key is 44 chars; anything near 512 is hostile input."""
    with pytest.raises(ValidationError):
        CreateConversionJobRequest(
            source_format="txt",
            target_format="pdf",
            input_key="a.txt",
            client_encrypted=True,
            data_key="A" * 513,
        )


def test_a_multipart_part_list_is_bounded() -> None:
    """S3 accepts at most 10 000 parts; a larger list is invalid, not bigger."""
    with pytest.raises(ValidationError):
        UploadVerifyRequest(parts=[UploadPartEtag(part_number=1, etag="x")] * 10_001)


def test_a_part_etag_is_length_bounded() -> None:
    with pytest.raises(ValidationError):
        UploadPartEtag(part_number=1, etag="x" * 129)


def test_a_normal_multipart_verify_body_is_accepted() -> None:
    body = UploadVerifyRequest(parts=[UploadPartEtag(part_number=1, etag='"abc"')])
    assert body.parts is not None
    assert len(body.parts) == 1
