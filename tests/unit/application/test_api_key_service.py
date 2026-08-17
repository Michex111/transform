"""Tests for the API key application service."""

import asyncio

import pytest

from src.application.services.api_key_service import APIKeyService, hash_api_key
from src.domain.security.enitities.api_key import APIKey, APIKeyStatus


class FakeAPIKeyRepository:
    """In-memory repository implementing the APIKeyRepositoryPort interface."""

    def __init__(self) -> None:
        self._rows: dict[str, APIKey] = {}

    async def save(self, api_key: APIKey) -> None:
        self._rows[api_key.id] = api_key

    async def get_by_id(self, api_key_id: str) -> APIKey | None:
        return self._rows.get(api_key_id)

    async def find_by_key(self, key_hash: str) -> APIKey | None:
        for api_key in self._rows.values():
            if api_key.key == key_hash:
                return api_key
        return None

    async def find_by_user(self, user_id: int) -> list[APIKey]:
        return [k for k in self._rows.values() if int(k.user_id) == user_id]

    async def update(self, api_key: APIKey) -> None:
        self._rows[api_key.id] = api_key

    async def touch_last_used(self, api_key_id: str) -> None:
        api_key = self._rows.get(api_key_id)
        if api_key is not None:
            api_key.last_used_at = api_key.last_used_at or api_key.created_at

    async def delete(self, api_key_id: str) -> bool:
        return self._rows.pop(api_key_id, None) is not None


@pytest.fixture
def service() -> tuple[APIKeyService, FakeAPIKeyRepository]:
    repo = FakeAPIKeyRepository()
    return APIKeyService(repository=repo), repo


def test_create_key_returns_plaintext_once_and_stores_hash(service) -> None:
    svc, repo = service
    plaintext, api_key = asyncio.run(svc.create(user_id=7, name="dev-key"))

    assert plaintext.startswith("tr_")
    assert api_key.key == hash_api_key(plaintext)
    assert api_key.key != plaintext
    stored = asyncio.run(repo.get_by_id(api_key.id))
    assert stored is not None
    assert stored.key == hash_api_key(plaintext)


def test_create_key_applies_expiration(service) -> None:
    svc, _ = service
    _, api_key = asyncio.run(svc.create(user_id=7, name="temp", expires_in_days=30))
    assert api_key.expires_at is not None

    _, no_expiry = asyncio.run(svc.create(user_id=7, name="forever", expires_in_days=None))
    assert no_expiry.expires_at is None


def test_list_for_user_filters_by_owner(service) -> None:
    svc, _ = service
    asyncio.run(svc.create(user_id=1, name="a"))
    asyncio.run(svc.create(user_id=1, name="b"))
    asyncio.run(svc.create(user_id=2, name="c"))

    keys = asyncio.run(svc.list_for_user(1))
    assert len(keys) == 2
    assert all(int(k.user_id) == 1 for k in keys)


def test_revoke_updates_status_and_prevents_auth(service) -> None:
    svc, _ = service
    plaintext, api_key = asyncio.run(svc.create(user_id=1, name="a"))

    revoked = asyncio.run(svc.revoke(api_key.id, user_id=1))
    assert revoked is not None
    assert revoked.status == APIKeyStatus.REVOKED

    assert asyncio.run(svc.authenticate(plaintext)) is None


def test_revoke_rejects_other_users(service) -> None:
    svc, _ = service
    _, api_key = asyncio.run(svc.create(user_id=1, name="a"))
    assert asyncio.run(svc.revoke(api_key.id, user_id=2)) is None


def test_delete_removes_key(service) -> None:
    svc, repo = service
    _, api_key = asyncio.run(svc.create(user_id=1, name="a"))
    assert asyncio.run(svc.delete(api_key.id, user_id=1)) is True
    assert asyncio.run(repo.get_by_id(api_key.id)) is None
    # Deleting again or by another user fails
    assert asyncio.run(svc.delete(api_key.id, user_id=1)) is False


def test_authenticate_valid_key_touches_last_used(service) -> None:
    svc, _ = service
    plaintext, api_key = asyncio.run(svc.create(user_id=1, name="a"))

    resolved = asyncio.run(svc.authenticate(plaintext))
    assert resolved is not None
    assert resolved.id == api_key.id
    assert resolved.last_used_at is not None


def test_authenticate_unknown_key_returns_none(service) -> None:
    svc, _ = service
    assert asyncio.run(svc.authenticate("tr_does-not-exist")) is None
