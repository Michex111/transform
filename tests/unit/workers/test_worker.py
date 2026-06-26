import asyncio

import pytest

import workers.converter_workers.worker as worker_module
from src.domain.value_object.job_status import JobStatus
from tests.fakes.fake_logger import FakeLogger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker


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