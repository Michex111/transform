import asyncio
from pathlib import Path

import pytest

from src.domain.value_object.job_status import JobStatus
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job


def test_event_publisher_collects_all_processing_events(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="event-worker",
    )

    asyncio.run(process_job(context, conversion_job))

    assert [event["message"] for event in fake_event_publisher.published_events] == [
        "downloading file",
        "converting file",
        "uploading file",
        "conversion completed",
    ]


def test_event_publisher_reports_completed_status_at_end(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="event-worker",
    )

    asyncio.run(process_job(context, conversion_job))

    last_event = fake_event_publisher.published_events[-1]
    assert last_event["status"] == str(JobStatus.COMPLETED)
    assert last_event["progress"] == 100


def test_event_publisher_stops_before_completed_event_on_failure(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path
        raise RuntimeError("conversion failed")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="event-worker",
    )

    with pytest.raises(RuntimeError):
        asyncio.run(process_job(context, conversion_job))

    assert fake_event_publisher.published_events[-1]["message"] != "conversion completed"