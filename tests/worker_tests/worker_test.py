import asyncio
from pathlib import Path

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.entities.conversion_job import ConversionJob
from src.domain.value_object.job_status import JobStatus
from src.domain.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import ConverterRegistry
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job
from workers.converter_workers.worker import ConverterWorker


class InMemoryQueue:
    def __init__(self):
        self.pending: list[tuple[str, ConversionJob]] = []
        self.sequence = 0
        self.acked: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.last_fetched_job: ConversionJob | None = None

    async def push_job(self, job: ConversionJob) -> None:
        self.sequence += 1
        self.pending.append((f"message-{self.sequence}", job))

    async def fetch_job(self) -> tuple[str, ConversionJob] | None:
        if not self.pending:
            return None
        message_id, job = self.pending.pop(0)
        self.last_fetched_job = job
        return message_id, job

    async def acknowledge_job(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def fail_job(self, message_id: str, error_message: str) -> None:
        self.failed.append((message_id, error_message))


class InMemoryStorage:
    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = objects or {}
        self.uploaded: dict[str, bytes] = {}

    def download(self, key: str, dest_path: Path) -> None:
        if key not in self.objects:
            raise FileNotFoundError(f"Object not found: {key}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(self.objects[key])

    def upload(self, target_key: str, source_path: Path) -> None:
        payload = source_path.read_bytes()
        self.objects[target_key] = payload
        self.uploaded[target_key] = payload


def test_worker_processes_job_successfully_via_conversion_service(monkeypatch: pytest.MonkeyPatch):
    conversion = ConversionType(source_format="txt", target_format="md")
    queue = InMemoryQueue()
    storage = InMemoryStorage(objects={"s3-file_store/input.txt": b"hello world"})
    service = ConversionService(queue_port=queue)

    registry = ConverterRegistry()

    @registry.register(conversion)
    def uppercase_converter(input_path: str, output_path: str) -> None:
        content = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(content.upper(), encoding="utf-8")

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: registry)

    job = ConversionJob(
        job_id="job-success",
        conversion=conversion,
        input_file="s3-file_store/input.txt",
    )

    asyncio.run(service.submit_conversion_job(job))

    context = WorkerContext(
        storage_port=storage,
        queue_port=queue,
        converter_registry=registry,
        worker_name="test-worker-success",
    )

    worker = ConverterWorker(context=context, process_job=lambda _ctx, _job: None)

    def run_once_then_stop(inner_context: WorkerContext, queued_job: ConversionJob) -> None:
        try:
            process_job(inner_context, queued_job)
        finally:
            worker.stop()

    worker.process_job = run_once_then_stop
    asyncio.run(worker.run())

    assert queue.acked == ["message-1"]
    assert queue.failed == []
    assert queue.last_fetched_job is not None
    assert queue.last_fetched_job.status == JobStatus.COMPLETED
    assert queue.last_fetched_job.output_file == "s3-file_store/input.md"
    assert storage.uploaded["s3-file_store/input.md"] == b"HELLO WORLD"


def test_worker_marks_job_failed_when_converter_raises(monkeypatch: pytest.MonkeyPatch):
    conversion = ConversionType(source_format="txt", target_format="md")
    queue = InMemoryQueue()
    storage = InMemoryStorage(objects={"s3-file_store/input.txt": b"hello world"})
    service = ConversionService(queue_port=queue)

    registry = ConverterRegistry()

    @registry.register(conversion)
    def failing_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path
        raise RuntimeError("converter exploded")

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: registry)

    job = ConversionJob(
        job_id="job-failed",
        conversion=conversion,
        input_file="s3-file_store/input.txt",
    )

    asyncio.run(service.submit_conversion_job(job))

    context = WorkerContext(
        storage_port=storage,
        queue_port=queue,
        converter_registry=registry,
        worker_name="test-worker-failure",
    )

    worker = ConverterWorker(context=context, process_job=lambda _ctx, _job: None)

    def run_once_then_stop(inner_context: WorkerContext, queued_job: ConversionJob) -> None:
        try:
            process_job(inner_context, queued_job)
        finally:
            worker.stop()

    worker.process_job = run_once_then_stop
    asyncio.run(worker.run())

    assert queue.acked == []
    assert len(queue.failed) == 1
    message_id, error_message = queue.failed[0]
    assert message_id == "message-1"
    assert "converter exploded" in error_message
    assert queue.last_fetched_job is not None
    assert queue.last_fetched_job.status == JobStatus.FAILED
    assert queue.last_fetched_job.error_message == "converter exploded"
