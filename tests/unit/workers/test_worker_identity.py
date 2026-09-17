"""Tests for worker consumer identity.

Redis Streams identify consumers by name. If every replica shares one name they
collapse into a single consumer identity, so the group's `pending`/`idle`
figures become aggregates and the stale-job reclaimer can steal messages that a
live replica is still processing.
"""

import os

import workers.converter_workers.main as main_module
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
