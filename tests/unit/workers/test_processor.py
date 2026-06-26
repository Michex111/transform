import asyncio
from pathlib import Path

import pytest

import workers.converter_workers.processor as processor_module
from src.domain.value_object.conversion_type import ConversionType
from src.domain.value_object.job_status import JobStatus
from tests.fakes.fake_logger import FakeLogger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job, resolve_path


def test_resolve_path_builds_download_and_upload_paths() -> None:
    input_file, output_file = resolve_path(
        "s3-file_store/manual.txt",
        ConversionType(source_format="txt", target_format="md"),
        Path("/tmp/work"),
    )

    assert input_file.name == "manual.txt"
    assert output_file.name == "manual.md"
    assert input_file.parent.name == "downloads"
    assert output_file.parent.name == "uploads"


def test_process_job_downloads_converts_and_uploads_successfully(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    logger = FakeLogger()
    monkeypatch.setattr(processor_module, "worker_logger", logger)
    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert conversion_job.output_file == "s3-file_store/input.md"
    assert fake_storage_port.objects["s3-file_store/input.md"] == b"HELLO WORLD"
    assert len(fake_storage_port.download_calls) == 1
    assert len(fake_storage_port.upload_calls) == 1


def test_process_job_raises_and_marks_failed_when_converter_missing(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    with pytest.raises(RuntimeError, match="No converter found"):
        asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.FAILED


def test_process_job_marks_failed_when_converter_raises(
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
        raise RuntimeError("converter exploded")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    with pytest.raises(RuntimeError, match="converter exploded"):
        asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.FAILED
    assert conversion_job.error_message == "converter exploded"


def test_process_job_publishes_expected_event_sequence(
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
        worker_name="processor-test",
    )

    asyncio.run(process_job(context, conversion_job))

    assert [event["progress"] for event in fake_event_publisher.published_events] == [25, 50, 75, 100]


def test_process_job_cleans_up_temporary_download_path(
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
        worker_name="processor-test",
    )

    asyncio.run(process_job(context, conversion_job))

    _, downloaded_path = fake_storage_port.download_calls[0]
    assert not downloaded_path.exists()


def test_process_job_emits_start_log_message(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    fake_logger = FakeLogger()
    monkeypatch.setattr(processor_module, "worker_logger", fake_logger)
    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    asyncio.run(process_job(context, conversion_job))

    assert any(
        level == "info" and "Starting processing job" in message
        for level, message, _ in fake_logger.records
    )