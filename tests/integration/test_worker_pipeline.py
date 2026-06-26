import asyncio
from pathlib import Path

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from tests.fakes.fake_logger import FakeLogger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job
from workers.converter_workers.worker import ConverterWorker


def test_worker_pipeline_runs_through_service_queue_and_processor(
    conversion_job,
    fake_queue_port,
    fake_storage_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: fake_converter_registry)
    service = ConversionService(queue_port=fake_queue_port)
    asyncio.run(service.submit_conversion_job(conversion_job))

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="integration-worker",
    )
    worker = ConverterWorker(context=context, process_job=process_job)

    fake_logger = FakeLogger()
    import workers.converter_workers.worker as worker_module

    monkeypatch.setattr(worker_module, "worker_logger", fake_logger)

    async def run_once_then_stop() -> None:
        await worker.run()

    async def stopper() -> None:
        while not fake_queue_port.acked_messages and not fake_queue_port.failed_messages:
            await asyncio.sleep(0.01)
        worker.stop()

    async def orchestrate() -> None:
        await asyncio.gather(run_once_then_stop(), stopper())

    asyncio.run(orchestrate())

    assert fake_queue_port.acked_messages == ["message-1"]
    assert fake_queue_port.failed_messages == []
    assert fake_storage_port.objects["s3-file_store/input.md"] == b"HELLO WORLD"
    assert any(event["progress"] == 100 for event in fake_event_publisher.published_events)
    assert any(
        level == "info" and "completed successfully" in message
        for level, message, _ in fake_logger.records
    )