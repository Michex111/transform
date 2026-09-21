"""Tests for worker consumer identity.

Redis Streams identify consumers by name. If every replica shares one name they
collapse into a single consumer identity, so the group's `pending`/`idle`
figures become aggregates and the stale-job reclaimer can steal messages that a
live replica is still processing.
"""

import asyncio
import os

import pytest

import workers.converter_workers.main as main_module
from src.infrastructure.config.settings import Settings
from workers.converter_workers.main import _default_worker_name


def test_default_worker_name_is_unique_per_process(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_NAME", raising=False)

    first = _default_worker_name()
    assert first.startswith("file_converter_worker-")

    # A different PID (i.e. another replica) must produce a different name.
    monkeypatch.setattr(os, "getpid", lambda: 999_999)
    second = _default_worker_name()

    assert first != second


def test_default_worker_name_includes_host_and_pid(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_NAME", raising=False)
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "test-host")

    assert _default_worker_name() == "file_converter_worker-test-host-4242"


def test_default_worker_name_honours_env_override(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_NAME", "my-stable-worker")

    assert _default_worker_name() == "my-stable-worker"


def test_default_consumer_group_is_unchanged() -> None:
    """Wiring WORKER_CONSUMER_GROUP must not change behaviour when it is unset.

    The setting existed, was documented and shipped in ``.env.example``, but was
    read by nothing — the group name lived only as a literal in ``build_worker``.
    """
    assert Settings.model_fields["WORKER_CONSUMER_GROUP"].default == "conversion-workers"


def test_build_worker_reads_the_consumer_group_from_settings(monkeypatch) -> None:
    """The setting is now actually honoured (W-DEAD-1)."""
    captured: dict[str, str] = {}

    async def fake_get_consumer_queue(*, consumer_group: str, consumer_name: str):
        captured["consumer_group"] = consumer_group
        captured["consumer_name"] = consumer_name
        return object()

    class FakeSettings:
        WORKER_CONSUMER_GROUP = "custom-group"

    monkeypatch.setattr(main_module, "get_consumer_queue", fake_get_consumer_queue)
    monkeypatch.setattr(main_module, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(main_module, "get_storage", lambda: object())
    monkeypatch.setattr(main_module, "get_event_queue", lambda: object())
    monkeypatch.setattr(main_module, "get_job_repository", lambda: None)
    monkeypatch.setattr(main_module, "get_credit_port", lambda: None)
    monkeypatch.setattr(main_module, "get_encryption_service", lambda: None)
    monkeypatch.setattr(main_module, "get_registry", lambda: object())

    worker = asyncio.run(main_module.build_worker("worker-x"))

    assert captured == {"consumer_group": "custom-group", "consumer_name": "worker-x"}
    assert worker is not None


def test_build_worker_uses_the_default_group_when_unset(monkeypatch) -> None:
    """Unset WORKER_CONSUMER_GROUP keeps the historical literal exactly."""
    captured: dict[str, str] = {}

    async def fake_get_consumer_queue(*, consumer_group: str, consumer_name: str):
        captured["consumer_group"] = consumer_group
        captured["consumer_name"] = consumer_name
        return object()

    class FakeSettings:
        WORKER_CONSUMER_GROUP = Settings.model_fields["WORKER_CONSUMER_GROUP"].default

    monkeypatch.setattr(main_module, "get_consumer_queue", fake_get_consumer_queue)
    monkeypatch.setattr(main_module, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(main_module, "get_storage", lambda: object())
    monkeypatch.setattr(main_module, "get_event_queue", lambda: object())
    monkeypatch.setattr(main_module, "get_job_repository", lambda: None)
    monkeypatch.setattr(main_module, "get_credit_port", lambda: None)
    monkeypatch.setattr(main_module, "get_encryption_service", lambda: None)
    monkeypatch.setattr(main_module, "get_registry", lambda: object())

    asyncio.run(main_module.build_worker("worker-x"))

    assert captured["consumer_group"] == "conversion-workers"


def test_redacted_redis_endpoint_hides_credentials(monkeypatch) -> None:
    """W-13: the startup line must never carry the Redis password."""

    class FakeSecret:
        @staticmethod
        def get_secret_value() -> str:
            return "rediss://default:sup3rs3cret@upstash.example:6380/1"

    class FakeSettings:
        REDIS_URL = FakeSecret()

    monkeypatch.setattr(main_module, "get_settings", lambda: FakeSettings())

    endpoint = main_module._redacted_redis_endpoint()

    assert endpoint == "upstash.example:6380/1"
    assert "sup3rs3cret" not in endpoint
    assert "default" not in endpoint
