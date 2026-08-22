"""Tests for the TransferService, especially download URL generation."""

import asyncio
from datetime import timedelta

import pytest

from src.application.dtos.upload_dto import UploadResponse
from src.application.exceptions.file_transfer_exceptions import (
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from src.application.services.file_transfer_service import TransferService


class FakeStorageGateway:
    """In-memory StorageUrlGateway recording URL generations."""

    def __init__(self) -> None:
        self.put_urls: list[str] = []
        self.get_urls: list[tuple[str, int]] = []
        self.objects: set[str] = set()

    def generate_put_url(self, object_key: str) -> str:
        self.put_urls.append(object_key)
        return f"https://storage.example/put/{object_key}"

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        self.get_urls.append((object_key, expires_in_minutes))
        return f"https://storage.example/get/{object_key}?expires={expires_in_minutes}"

    async def object_exists(self, object_key: str) -> bool:
        return object_key in self.objects


class FakeCache:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def format_key(self, key: str) -> str:
        return key

    async def set(self, key: str, data: str, ttl) -> None:
        self._data[self.format_key(key)] = data

    async def get(self, key: str) -> str | None:
        return self._data.get(self.format_key(key))

    async def delete(self, key: str) -> None:
        self._data.pop(self.format_key(key), None)


@pytest.fixture
def storage() -> FakeStorageGateway:
    return FakeStorageGateway()


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def service(storage: FakeStorageGateway, cache: FakeCache) -> TransferService:
    return TransferService(storage_port=storage, cache_port=cache, ttl_minutes=10)


def test_create_download_url_generates_presigned_get(storage, service) -> None:
    url = asyncio.run(service.create_download_url("output/report.pdf"))

    assert url == "https://storage.example/get/output/report.pdf?expires=10"
    assert storage.get_urls == [("output/report.pdf", 10)]


def test_create_download_url_uses_custom_expiry(storage, service) -> None:
    url = asyncio.run(service.create_download_url("output/report.pdf", expires_in_minutes=30))

    assert url == "https://storage.example/get/output/report.pdf?expires=30"
    assert storage.get_urls == [("output/report.pdf", 30)]


def test_create_upload_generates_put_url_and_sets_session(storage, cache, service) -> None:
    response: UploadResponse = asyncio.run(service.create_upload("pdf", "user-1"))

    assert response.upload_url == f"https://storage.example/put/{response.object_key}"
    assert storage.put_urls == [response.object_key]
    assert response.expires_in_minutes == 10

    # session is stored in the cache and recoverable
    session = asyncio.run(service.get_upload_session(response.upload_id))
    assert session.object_key == response.object_key
    assert session.status == "pending"


def test_get_upload_session_missing_raises(service) -> None:
    with pytest.raises(UploadSessionNotFoundError):
        asyncio.run(service.get_upload_session("does-not-exist"))


def test_verify_upload_completion_checks_object_exists(storage, service) -> None:
    response: UploadResponse = asyncio.run(service.create_upload("pdf", "user-1"))

    # object not yet uploaded
    with pytest.raises(UploadVerificationError):
        asyncio.run(service.verify_upload_completion(response.upload_id))

    # after the object exists, verification succeeds and marks it completed
    storage.objects.add(response.object_key)
    session = asyncio.run(service.verify_upload_completion(response.upload_id))
    assert session.status == "completed"
