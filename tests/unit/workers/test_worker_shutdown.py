"""Tests for graceful shutdown wiring in both workers (W-2).

Docker's ``stop`` and the ``restart: unless-stopped`` cycle send **SIGTERM**,
whose default disposition terminates the interpreter immediately: no
``finally``, no Redis ``aclose()``, and the in-flight job left un-ACKed. Only
``KeyboardInterrupt`` (SIGINT in a TTY) was handled before.
"""

import signal

import pytest

import workers.cleanup_worker.main as cleanup_main
import workers.converter_workers.main as converter_main
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker


class RecordingLoop:
    """Captures the callbacks registered via ``add_signal_handler``."""

    def __init__(self) -> None:
        self.handlers: dict[int, tuple] = {}

    def add_signal_handler(self, sig, callback, *args) -> None:  # noqa: ANN001
        self.handlers[sig] = (callback, args)


class StubCleanupWorker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_converter_worker_registers_sigterm_and_stops_gracefully(monkeypatch) -> None:
    loop = RecordingLoop()
    monkeypatch.setattr(converter_main.asyncio, "get_running_loop", lambda: loop)

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=None,  # type: ignore[arg-type]
            queue_port=None,  # type: ignore[arg-type]
            event_port=None,  # type: ignore[arg-type]
            converter_registry=None,  # type: ignore[arg-type]
            worker_name="shutdown-test",
        ),
        process_job=lambda context, job: None,  # type: ignore[arg-type,return-value]
    )
    worker._running = True

    converter_main._install_shutdown_handler(worker)

    assert signal.SIGTERM in loop.handlers
    assert signal.SIGINT in loop.handlers

    callback, args = loop.handlers[signal.SIGTERM]
    callback(*args)

    # The handler only flips the run flag, so the job being processed still
    # finishes and is ACKed before the loop exits.
    assert worker._running is False


def test_cleanup_worker_registers_sigterm_and_stops_gracefully(monkeypatch) -> None:
    loop = RecordingLoop()
    monkeypatch.setattr(cleanup_main.asyncio, "get_running_loop", lambda: loop)

    worker = StubCleanupWorker()

    cleanup_main._install_shutdown_handler(worker)  # type: ignore[arg-type]

    assert signal.SIGTERM in loop.handlers

    callback, args = loop.handlers[signal.SIGTERM]
    callback(*args)

    assert worker.stopped is True


def test_signal_handler_falls_back_when_the_loop_cannot_register(monkeypatch) -> None:
    """Platforms without ``add_signal_handler`` must still stop gracefully."""

    class UnsupportedLoop:
        def add_signal_handler(self, sig, callback, *args):  # noqa: ANN001
            del sig, callback, args
            raise NotImplementedError

    registered: dict[int, object] = {}

    def fake_signal(sig, handler):  # noqa: ANN001
        registered[sig] = handler

    monkeypatch.setattr(converter_main.asyncio, "get_running_loop", UnsupportedLoop)
    monkeypatch.setattr(converter_main.signal, "signal", fake_signal)

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=None,  # type: ignore[arg-type]
            queue_port=None,  # type: ignore[arg-type]
            event_port=None,  # type: ignore[arg-type]
            converter_registry=None,  # type: ignore[arg-type]
            worker_name="shutdown-test",
        ),
        process_job=lambda context, job: None,  # type: ignore[arg-type,return-value]
    )
    worker._running = True

    converter_main._install_shutdown_handler(worker)

    assert signal.SIGTERM in registered
    registered[signal.SIGTERM](signal.SIGTERM, None)  # type: ignore[operator]
    assert worker._running is False


def test_close_queue_client_closes_the_redis_client() -> None:
    """Shutdown must not leak the consumer's Redis connection."""

    class FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeQueue:
        def __init__(self) -> None:
            self.redis_client = FakeRedis()

    queue = FakeQueue()
    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=None,  # type: ignore[arg-type]
            queue_port=queue,  # type: ignore[arg-type]
            event_port=None,  # type: ignore[arg-type]
            converter_registry=None,  # type: ignore[arg-type]
            worker_name="shutdown-test",
        ),
        process_job=lambda context, job: None,  # type: ignore[arg-type,return-value]
    )

    import asyncio

    asyncio.run(converter_main._close_queue_client(worker))

    assert queue.redis_client.closed is True


def test_close_queue_client_tolerates_a_queue_without_a_client() -> None:
    """A fake/other port implementation must not break shutdown."""

    class FakeQueue:
        pass

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=None,  # type: ignore[arg-type]
            queue_port=FakeQueue(),  # type: ignore[arg-type]
            event_port=None,  # type: ignore[arg-type]
            converter_registry=None,  # type: ignore[arg-type]
            worker_name="shutdown-test",
        ),
        process_job=lambda context, job: None,  # type: ignore[arg-type,return-value]
    )

    import asyncio

    asyncio.run(converter_main._close_queue_client(worker))  # must not raise
