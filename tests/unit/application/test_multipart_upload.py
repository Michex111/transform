"""Tests for multipart upload planning and the part-URL service logic.

The real S3/B2 provider cannot be exercised here, so these drive the layer that
CAN be verified without a bucket: ``TransferService`` against a recording fake
gateway, plus the pure part-count arithmetic. What is therefore *not* covered
here is the provider's own behaviour (that a presigned part URL really accepts a
PUT); the adapter's call is asserted separately and the parameter shape is
documented in ``MinioUrlStorageAdapter``.
"""

import asyncio

import pytest

from src.application.dtos.upload_dto import UploadSession
from src.application.exceptions.file_transfer_exceptions import (
    InvalidPartNumberError,
    MissingUploadPartsError,
    UploadNotMultipartError,
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from src.application.services.file_transfer_service import TransferService, plan_multipart

THRESHOLD = 100
PART_SIZE = 10


class FakeGateway:
    """Records every multipart call so the orchestration can be asserted."""

    def __init__(self) -> None:
        self.put_urls: list[str] = []
        self.created: list[str] = []
        self.part_urls: list[tuple[str, int, str, int]] = []
        self.completed: list[tuple[str, str, list[tuple[int, str]]]] = []
        self.aborted: list[tuple[str, str]] = []
        self.objects: set[str] = set()

    def generate_put_url(self, object_key: str) -> str:
        self.put_urls.append(object_key)
        return f"https://storage.example/put/{object_key}"

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        return f"https://storage.example/get/{object_key}"

    async def object_exists(self, object_key: str) -> bool:
        return object_key in self.objects

    def create_multipart_upload(self, object_key: str) -> str:
        self.created.append(object_key)
        return f"provider-upload-{len(self.created)}"

    def generate_part_upload_url(
        self, object_key: str, part_number: int, upload_id: str, expires_in_minutes: int,
    ) -> str:
        self.part_urls.append((object_key, part_number, upload_id, expires_in_minutes))
        return f"https://storage.example/part/{upload_id}/{part_number}"

    async def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[tuple[int, str]],
    ) -> None:
        self.completed.append((object_key, upload_id, list(parts)))

    async def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        self.aborted.append((object_key, upload_id))


class FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, data: str, ttl) -> None:
        self.data[key] = data

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def service(gateway: FakeGateway, cache: FakeCache) -> TransferService:
    return TransferService(
        storage_port=gateway,
        cache_port=cache,
        ttl_minutes=15,
        large_ttl_minutes=120,
        multipart_threshold_bytes=THRESHOLD,
        multipart_part_size_bytes=PART_SIZE,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Part-count arithmetic
# ---------------------------------------------------------------------------

def test_plan_multipart_rounds_up_on_a_partial_final_part() -> None:
    assert plan_multipart(25, part_size=10) == (3, 10)


def test_plan_multipart_does_not_add_an_empty_trailing_part() -> None:
    """An exact multiple must not produce an extra zero-byte part."""
    assert plan_multipart(30, part_size=10) == (3, 10)


def test_plan_multipart_for_the_five_gib_ceiling_stays_under_the_s3_limit() -> None:
    """ceil(5 GiB / 64 MiB) = 80 parts, far under the 10 000-part S3 maximum."""
    part_count, part_size = plan_multipart(5 * 1024**3, part_size=64 * 1024**2)
    assert part_size == 64 * 1024**2
    assert part_count == 80


@pytest.mark.parametrize(("size", "part_size"), [(0, 10), (-1, 10), (10, 0)])
def test_plan_multipart_rejects_nonsense_inputs(size: int, part_size: int) -> None:
    with pytest.raises(ValueError):
        plan_multipart(size, part_size=part_size)


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

def test_omitting_file_size_keeps_the_single_put_path(service, gateway, cache) -> None:
    """The historical behaviour (guest flow, older clients) must be unchanged:
    one presigned PUT URL, no multipart, no provider call."""
    response = _run(service.create_upload("pdf", "user-1", file_name="a.pdf"))

    assert response.upload_mode == "single"
    assert response.upload_url is not None
    assert response.part_size_bytes is None
    assert response.part_count is None
    assert gateway.created == []
    assert gateway.put_urls == [response.object_key]
    # The session TTL/URL window stays the short one.
    assert response.expires_in_minutes == 15


def test_small_declared_size_still_uses_single_put(service, gateway) -> None:
    response = _run(service.create_upload("pdf", "user-1", file_size=THRESHOLD - 1))

    assert response.upload_mode == "single"
    assert response.upload_url is not None
    assert gateway.created == []


def test_declared_size_at_the_threshold_switches_to_multipart(service, gateway) -> None:
    """Boundary: exactly at the threshold uses multipart (matching the >= rule)."""
    response = _run(service.create_upload("pdf", "user-1", file_size=THRESHOLD))

    assert response.upload_mode == "multipart"
    assert response.upload_url is None
    assert response.part_size_bytes == PART_SIZE
    assert response.part_count == THRESHOLD // PART_SIZE


def test_multipart_session_is_created_eagerly_and_the_id_is_persisted(
    service, gateway, cache
) -> None:
    """The provider upload id must survive into the cached session, or the
    later /parts and /verify calls have nothing to address."""
    response = _run(service.create_upload("bin", "user-1", file_size=THRESHOLD))

    assert gateway.created == [response.object_key]
    session = _run(service.get_upload_session(response.upload_id))
    assert session.multipart_upload_id == "provider-upload-1"
    assert session.upload_mode == "multipart"
    assert session.declared_size == THRESHOLD
    assert session.part_count == THRESHOLD // PART_SIZE


def test_multipart_session_gets_the_long_url_window(service) -> None:
    """A 5 GiB transfer outlives a 15-minute window, so multipart sessions use
    the large TTL for the session and its part URLs."""
    response = _run(service.create_upload("bin", "user-1", file_size=THRESHOLD))

    assert response.expires_in_minutes == 120


def test_max_file_size_is_echoed_back(service) -> None:
    response = _run(
        service.create_upload("bin", "user-1", file_size=THRESHOLD, max_file_size_bytes=42)
    )
    assert response.max_file_size_bytes == 42


# ---------------------------------------------------------------------------
# Part URL batches
# ---------------------------------------------------------------------------

def _multipart_session(service) -> UploadSession:
    response = _run(service.create_upload("bin", "user-1", file_size=THRESHOLD))
    return _run(service.get_upload_session(response.upload_id))


def test_part_urls_are_returned_ascending_and_deduplicated(service, gateway) -> None:
    session = _multipart_session(service)

    parts = _run(service.generate_part_urls(session, [3, 1, 2, 1]))

    assert [number for number, _ in parts] == [1, 2, 3]
    assert [number for _, number, _, _ in gateway.part_urls] == [1, 2, 3]
    # Every URL is addressed to this session's provider upload id and the long
    # TTL window.
    assert {upload_id for _, _, upload_id, _ in gateway.part_urls} == {"provider-upload-1"}
    assert {ttl for _, _, _, ttl in gateway.part_urls} == {120}


def test_a_full_batch_of_one_hundred_parts_is_accepted(service, gateway) -> None:
    """The endpoint's per-call cap is 100; a session must be able to serve it."""
    response = _run(
        service.create_upload("bin", "user-1", file_size=PART_SIZE * 100)
    )
    session = _run(service.get_upload_session(response.upload_id))
    assert session.part_count == 100

    parts = _run(service.generate_part_urls(session, list(range(1, 101))))

    assert len(parts) == 100
    assert [number for number, _ in parts] == list(range(1, 101))


@pytest.mark.parametrize("number", [0, -1, 11])
def test_out_of_range_part_numbers_are_rejected(service, number: int) -> None:
    """Minting a URL the provider would reject wastes a round trip and surfaces
    as an opaque 400 from the bucket much later."""
    session = _multipart_session(service)  # part_count == 10

    with pytest.raises(InvalidPartNumberError):
        _run(service.generate_part_urls(session, [number]))


def test_part_urls_for_a_single_put_session_are_a_conflict(service) -> None:
    response = _run(service.create_upload("pdf", "user-1", file_size=THRESHOLD - 1))
    session = _run(service.get_upload_session(response.upload_id))

    with pytest.raises(UploadNotMultipartError):
        _run(service.generate_part_urls(session, [1]))


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

def test_verify_completes_a_multipart_upload_with_ascending_parts(
    service, gateway
) -> None:
    session = _multipart_session(service)
    gateway.objects.add(session.object_key)

    result = _run(
        service.verify_upload_completion(
            session.upload_id,
            parts=[(3, '"c"'), (1, '"a"'), (2, '"b"')],
        )
    )

    assert gateway.completed == [
        (session.object_key, "provider-upload-1", [(1, '"a"'), (2, '"b"'), (3, '"c"')])
    ]
    assert result.status == "completed"


def test_verify_clears_the_multipart_id_so_a_retry_cannot_complete_twice(
    service, gateway
) -> None:
    """The provider rejects a second CompleteMultipartUpload on the same upload,
    so a retry after a later failure must not attempt it."""
    session = _multipart_session(service)
    gateway.objects.add(session.object_key)

    _run(service.verify_upload_completion(session.upload_id, parts=[(1, '"a"')]))
    # A retry without a body now simply re-checks the assembled object.
    again = _run(service.verify_upload_completion(session.upload_id))

    assert again.status == "completed"
    assert len(gateway.completed) == 1


def test_verify_of_a_multipart_session_requires_the_parts(service, gateway) -> None:
    session = _multipart_session(service)
    gateway.objects.add(session.object_key)

    with pytest.raises(MissingUploadPartsError):
        _run(service.verify_upload_completion(session.upload_id))
    assert gateway.completed == []


def test_verify_of_a_single_session_never_completes_a_multipart_upload(
    service, gateway
) -> None:
    response = _run(service.create_upload("pdf", "user-1", file_size=THRESHOLD - 1))
    gateway.objects.add(response.object_key)

    session = _run(service.verify_upload_completion(response.upload_id))

    assert session.status == "completed"
    assert gateway.completed == []


def test_verify_still_fails_when_the_object_is_absent(service, gateway) -> None:
    session = _multipart_session(service)

    with pytest.raises(UploadVerificationError):
        _run(service.verify_upload_completion(session.upload_id, parts=[(1, '"a"')]))


def test_verify_of_an_unknown_session_raises_not_found(service) -> None:
    with pytest.raises(UploadSessionNotFoundError):
        _run(service.verify_upload_completion("nope"))


# ---------------------------------------------------------------------------
# Delete / abort
# ---------------------------------------------------------------------------

def test_deleting_a_multipart_session_aborts_the_provider_upload(
    service, gateway, cache
) -> None:
    """Abandoning a large upload must release its parts instead of leaving them
    for a lifecycle rule to eventually reap."""
    session = _multipart_session(service)

    _run(service.delete_upload_session(session.upload_id))

    assert gateway.aborted == [(session.object_key, "provider-upload-1")]
    assert session.upload_id not in cache.data


def test_deleting_a_single_session_does_not_abort_anything(service, gateway) -> None:
    response = _run(service.create_upload("pdf", "user-1"))

    _run(service.delete_upload_session(response.upload_id))

    assert gateway.aborted == []


def test_deleting_an_aborted_multipart_session_does_not_abort_twice(
    service, gateway
) -> None:
    session = _multipart_session(service)
    gateway.objects.add(session.object_key)

    _run(service.verify_upload_completion(session.upload_id, parts=[(1, '"a"')]))
    _run(service.delete_upload_session(session.upload_id))

    assert gateway.aborted == []


def test_abort_failures_do_not_block_the_delete(service, gateway, cache) -> None:
    """A provider hiccup must not turn cleanup into an error the caller cannot
    act on."""
    session = _multipart_session(service)

    async def boom(object_key: str, upload_id: str) -> None:
        raise RuntimeError("provider down")

    gateway.abort_multipart_upload = boom  # type: ignore[method-assign]

    _run(service.delete_upload_session(session.upload_id))

    assert session.upload_id not in cache.data
