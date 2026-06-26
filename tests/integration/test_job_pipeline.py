import asyncio
from pathlib import Path

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.value_object.job_status import JobStatus
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job


def test_job_pipeline_runs_end_to_end_in_memory(
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
        Path(output_path).write_text(text.replace("hello", "goodbye"), encoding="utf-8")

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: fake_converter_registry)
    service = ConversionService(queue_port=fake_queue_port)

    returned_id = asyncio.run(service.submit_conversion_job(conversion_job))
    message_id, queued_job = asyncio.run(fake_queue_port.fetch_job())

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="integration-worker",
    )
    asyncio.run(process_job(context, queued_job))
    asyncio.run(fake_queue_port.acknowledge_job(message_id))

    assert returned_id == conversion_job.job_id
    assert queued_job.status == JobStatus.COMPLETED
    assert queued_job.output_file == "s3-file_store/input.md"
    assert fake_storage_port.objects["s3-file_store/input.md"] == b"goodbye world"
    assert fake_queue_port.acked_messages == ["message-1"]
    assert fake_event_publisher.published_events[-1]["progress"] == 100