import asyncio

import pytest

import workers.converter_workers.worker as worker_module
from src.domain.conversions.value_object.job_status import JobStatus
from tests.fakes.fake_logger import FakeLogger
from tests.fakes.fake_queue import FakeQueuePort
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker


def _make_worker(queue_port, **kwargs) -> ConverterWorker:
    """Build a worker wired to the given queue port and the standard fakes."""
    context = WorkerContext(
        storage_port=kwargs.pop("storage_port"),
        queue_port=queue_port,
        event_port=kwargs.pop("event_port"),
        converter_registry=kwargs.pop("converter_registry"),
        worker_name="worker-test",
    )
    return ConverterWorker(context=context, process_job=kwargs.pop("process_job"))


class ExplodingFailQueue(FakeQueuePort):
    """Queue whose ``fail_job`` raises, as a Redis error mid-failure-handler does."""

    async def fail_job(self, message_id: str, error_message: str) -> None:
        self.events.append(("fail", message_id))
        raise RuntimeError("redis is down")


def test_worker_acknowledges_message_when_process_succeeds(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    fake_queue_port.preload(conversion_job, message_id="message-1")

    async def process_once(context: WorkerContext, job) -> None:
        del context
        job.status = JobStatus.COMPLETED
        worker.stop()

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=fake_storage_port,
            queue_port=fake_queue_port,
            event_port=fake_event_publisher,
            converter_registry=fake_converter_registry,
            worker_name="worker-test",
        ),
        process_job=process_once,
    )

    asyncio.run(worker.run())

    assert fake_queue_port.acked_messages == ["message-1"]
    assert fake_queue_port.failed_messages == []


def test_worker_marks_queue_message_failed_when_process_raises(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    fake_queue_port.preload(conversion_job, message_id="message-9")

    async def process_and_fail(context: WorkerContext, job) -> None:
        del context
        del job
        worker.stop()
        raise RuntimeError("failed in process")

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=fake_storage_port,
            queue_port=fake_queue_port,
            event_port=fake_event_publisher,
            converter_registry=fake_converter_registry,
            worker_name="worker-test",
        ),
        process_job=process_and_fail,
    )

    asyncio.run(worker.run())

    assert fake_queue_port.acked_messages == []
    assert fake_queue_port.failed_messages == [("message-9", "failed in process")]


def test_worker_dead_letters_failed_job(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """Failed jobs must be copied to the dead-letter stream for replay."""
    fake_queue_port.preload(conversion_job, message_id="message-11")

    async def process_and_fail(context: WorkerContext, job) -> None:
        del context
        del job
        worker.stop()
        raise RuntimeError("boom")

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=fake_storage_port,
            queue_port=fake_queue_port,
            event_port=fake_event_publisher,
            converter_registry=fake_converter_registry,
            worker_name="worker-test",
        ),
        process_job=process_and_fail,
    )

    asyncio.run(worker.run())

    assert len(fake_queue_port.dead_lettered) == 1
    message_id, error, job = fake_queue_port.dead_lettered[0]
    assert message_id == "message-11"
    assert error == "boom"
    assert job.job_id == conversion_job.job_id
    # The pending message is still acked (fail_job) so it does not redeliver.
    assert fake_queue_port.failed_messages == [("message-11", "boom")]
    # ORDERING: the dead letter is written BEFORE the ACK. ``fail_job`` is a
    # bare XACK, so ACKing first would remove the only copy of the job before
    # the DLQ copy exists — a failing XADD would then lose it entirely.
    assert [step for step, _ in fake_queue_port.events] == ["fetch", "dead_letter", "fail"]


def test_worker_still_dead_letters_and_logs_when_the_ack_fails(
    conversion_job,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis error inside ``fail_job`` must not skip the DLQ write or the log.

    ``fail_job`` used to be called outside the try/except, so its error
    propagated out of the failure handler: no dead letter, no useful error log,
    and the outer loop's generic handler ran instead.
    """
    queue_port = ExplodingFailQueue()
    queue_port.preload(conversion_job, message_id="message-13")
    logger = FakeLogger()
    monkeypatch.setattr(worker_module, "worker_logger", logger)

    async def process_and_fail(context: WorkerContext, job) -> None:
        del context
        del job
        worker.stop()
        raise RuntimeError("boom")

    worker = _make_worker(
        queue_port,
        storage_port=fake_storage_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        process_job=process_and_fail,
    )

    asyncio.run(worker.run())

    assert [step for step, _ in queue_port.events] == ["fetch", "dead_letter", "fail"]
    assert len(queue_port.dead_lettered) == 1
    assert any(
        level == "error" and "Failed to acknowledge failed job" in message
        for level, message, _ in logger.records
    )
    assert any(
        level == "error" and "Error processing job" in message
        for level, message, _ in logger.records
    )


def test_worker_propagates_cancellation_after_cleanup(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CancelledError must be re-raised once the loop has stopped cleanly.

    Swallowing it (``stop()`` then ``return``) makes the caller believe
    shutdown completed while the task is still awaited, which breaks the
    cancellation path now that SIGTERM is handled gracefully.
    """
    fake_queue_port.preload(conversion_job, message_id="message-21")
    logger = FakeLogger()
    monkeypatch.setattr(worker_module, "worker_logger", logger)

    async def process_cancelled(context: WorkerContext, job) -> None:
        del context
        del job
        raise asyncio.CancelledError()

    worker = _make_worker(
        fake_queue_port,
        storage_port=fake_storage_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        process_job=process_cancelled,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run())

    assert any(
        level == "info" and "received shutdown signal" in message
        for level, message, _ in logger.records
    )
    # The interrupted job stays un-ACKed on purpose: the stale-pending sweep
    # re-queues it rather than the worker pretending it succeeded.
    assert fake_queue_port.acked_messages == []


def test_worker_logs_startup_and_shutdown(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_queue_port.preload(conversion_job, message_id="message-7")
    fake_logger = FakeLogger()
    monkeypatch.setattr(worker_module, "worker_logger", fake_logger)

    async def process_once(context: WorkerContext, job) -> None:
        del context
        del job
        worker.stop()

    worker = ConverterWorker(
        context=WorkerContext(
            storage_port=fake_storage_port,
            queue_port=fake_queue_port,
            event_port=fake_event_publisher,
            converter_registry=fake_converter_registry,
            worker_name="worker-test",
        ),
        process_job=process_once,
    )

    asyncio.run(worker.run())

    assert any(msg == "Converter worker started" for level, msg, _ in fake_logger.records if level == "info")
    assert any(msg == "Converter worker stopped" for level, msg, _ in fake_logger.records if level == "info")