"""Security regression tests for object-key and conversion-job ownership.

Covers:
- SEC-1: ``POST /api/v1/files/urls`` must only presign the caller's own keys.
- SEC-4/DEDUP-3: an authenticated user must not read an ownerless (guest) job
  or another user's job through the authenticated conversions router.
"""

import asyncio

import pytest
from fastapi import HTTPException

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.presentation.api.routers.v1 import conversions as conversions_router
from src.presentation.api.routers.v1 import files as files_router
from src.presentation.schemas.files import PresignedUrlsRequest


def _run(coro):
    return asyncio.run(coro)


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeUrlStorage:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing
        self.get_url_calls: list[str] = []
        self.exists_calls: list[str] = []

    async def object_exists(self, object_key: str) -> bool:
        self.exists_calls.append(object_key)
        return object_key in self._existing

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        del expires_in_minutes
        self.get_url_calls.append(object_key)
        return f"https://storage.test/{object_key}"


class FakeUserFileRepo:
    def __init__(self, owned: set[str]) -> None:
        self._owned = owned

    async def list_owned_keys(self, user_id: int, file_keys: list[str]) -> set[str]:
        del user_id
        return {key for key in file_keys if key in self._owned}


class FakeJobKeyRepo:
    def __init__(self, owned: set[str]) -> None:
        self._owned = owned

    async def list_owned_object_keys(self, user_id: int, object_keys: list[str]) -> set[str]:
        del user_id
        return {key for key in object_keys if key in self._owned}


# ---------------------------------------------------------------------------
# SEC-1 — presigned URLs
# ---------------------------------------------------------------------------

def test_presigned_urls_returns_url_for_an_owned_key() -> None:
    storage = FakeUrlStorage(existing={"uploads/mine.pdf"})
    result = _run(
        files_router.generate_presigned_urls(
            PresignedUrlsRequest(object_keys=["uploads/mine.pdf"]),
            _User(1),
            storage,
            FakeUserFileRepo(owned={"uploads/mine.pdf"}),
            FakeJobKeyRepo(owned=set()),
        )
    )

    assert len(result) == 1
    assert result[0].object_key == "uploads/mine.pdf"
    assert result[0].url.endswith("uploads/mine.pdf")


def test_presigned_urls_allows_a_conversion_job_output_key() -> None:
    """The SPA checks ``job.object_key`` for its own jobs — that must work."""
    storage = FakeUrlStorage(existing={"conversions/out.docx"})
    result = _run(
        files_router.generate_presigned_urls(
            PresignedUrlsRequest(object_keys=["conversions/out.docx"]),
            _User(1),
            storage,
            FakeUserFileRepo(owned=set()),
            FakeJobKeyRepo(owned={"conversions/out.docx"}),
        )
    )
    assert [r.object_key for r in result] == ["conversions/out.docx"]


def test_presigned_urls_rejects_a_key_the_caller_does_not_own() -> None:
    # The object EXISTS in the bucket (another tenant's), but not for this user.
    storage = FakeUrlStorage(existing={"uploads/someone-else.pdf"})
    with pytest.raises(HTTPException) as exc:
        _run(
            files_router.generate_presigned_urls(
                PresignedUrlsRequest(object_keys=["uploads/someone-else.pdf"]),
                _User(1),
                storage,
                FakeUserFileRepo(owned=set()),
                FakeJobKeyRepo(owned=set()),
            )
        )

    assert exc.value.status_code == 404
    # The non-owned key must never be probed, so its existence cannot leak.
    assert storage.get_url_calls == []
    assert storage.exists_calls == []


def test_presigned_urls_rejects_traversal_key() -> None:
    storage = FakeUrlStorage(existing={"../secret"})
    with pytest.raises(HTTPException) as exc:
        _run(
            files_router.generate_presigned_urls(
                PresignedUrlsRequest(object_keys=["../secret"]),
                _User(1),
                storage,
                FakeUserFileRepo(owned=set()),
                FakeJobKeyRepo(owned=set()),
            )
        )
    assert exc.value.status_code == 404


def test_presigned_urls_raises_on_first_missing_key_in_order() -> None:
    storage = FakeUrlStorage(existing={"uploads/a.pdf"})
    with pytest.raises(HTTPException) as exc:
        _run(
            files_router.generate_presigned_urls(
                PresignedUrlsRequest(object_keys=["uploads/a.pdf", "uploads/missing.pdf"]),
                _User(1),
                storage,
                FakeUserFileRepo(owned={"uploads/a.pdf", "uploads/missing.pdf"}),
                FakeJobKeyRepo(owned=set()),
            )
        )
    assert exc.value.status_code == 404
    assert "uploads/missing.pdf" in exc.value.detail


# ---------------------------------------------------------------------------
# SEC-4 — guest jobs are not readable through the authenticated router
# ---------------------------------------------------------------------------

class FakeJobRepository:
    def __init__(self, job: ConversionJob | None) -> None:
        self._job = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        del job_id
        return self._job


def _job(user_id: int | None) -> ConversionJob:
    return ConversionJob(
        job_id="job-1",
        conversion=ConversionType("pdf", "docx"),
        input_file="in.pdf",
        output_file="conversions/out.docx",
        object_key="uploads/in.pdf",
        user_id=user_id,
    )


def test_authenticated_router_hides_ownerless_guest_job() -> None:
    with pytest.raises(HTTPException) as exc:
        _run(
            conversions_router.get_conversion_job(
                "job-1",
                _User(1),
                FakeJobRepository(_job(user_id=None)),
                _FakeTransferService(),
                None,
            )
        )
    assert exc.value.status_code == 404


def test_authenticated_router_hides_another_users_job() -> None:
    with pytest.raises(HTTPException) as exc:
        _run(
            conversions_router.get_conversion_job(
                "job-1",
                _User(1),
                FakeJobRepository(_job(user_id=2)),
                _FakeTransferService(),
                None,
            )
        )
    assert exc.value.status_code == 404


def test_authenticated_router_returns_own_job() -> None:
    response = _run(
        conversions_router.get_conversion_job(
            "job-1",
            _User(1),
            FakeJobRepository(_job(user_id=1)),
            _FakeTransferService(),
            None,
        )
    )
    assert response.job_id == "job-1"


class _FakeTransferService:
    async def create_download_url(self, object_key: str, *, expires_in_minutes: int) -> str:
        del object_key, expires_in_minutes
        return "https://storage.test/presigned"
