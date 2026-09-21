import asyncio
import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.converters.converter_registry import get_registry
from tests.fakes.fake_logger import FakeLogger
from tests.fakes.fake_queue import FakeQueuePort
from tests.fakes.fake_storage import FakeStoragePort
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import process_job
from workers.converter_workers.worker import ConverterWorker


def test_worker_pipeline_runs_through_service_queue_and_processor(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
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
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)
    conversion_job.pending_processing()
    asyncio.run(service.push_conversion_job(conversion_job))

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
    assert fake_storage_port.objects["output/user/guest/job/job-1/input.md"] == b"HELLO WORLD"
    assert any(event["progress"] == 100 for event in fake_event_publisher.published_events)
    assert any(
        level == "info" and "completed successfully" in message
        for level, message, _ in fake_logger.records
    )


def test_process_job_runs_the_real_image_to_pdf_converter(
    conversion_job_factory,
    fake_event_publisher,
) -> None:
    """png -> pdf goes through the real registry, not a stub.

    The stored object must be a genuine single-page PDF named for the target
    format: no container hook, no mislabelled extension.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 240)).save(buffer, "PNG")
    storage = FakeStoragePort(seed_files={"s3-file_store/photo.png": buffer.getvalue()})
    job = conversion_job_factory(
        job_id="job-png",
        source_format="png",
        target_format="pdf",
        input_file="s3-file_store/photo.png",
        object_key="s3-file_store/photo.png",
    )
    context = WorkerContext(
        storage_port=storage,
        queue_port=FakeQueuePort(),
        event_port=fake_event_publisher,
        converter_registry=get_registry(),
        worker_name="integration-worker",
    )

    job.pending_processing()
    asyncio.run(process_job(context, job))

    assert job.status == JobStatus.COMPLETED
    assert job.output_file is not None
    assert job.output_file.endswith(".pdf")

    stored = storage.objects[job.output_file]
    assert stored.startswith(b"%PDF-")
    document = pdfium.PdfDocument(stored)
    try:
        assert len(document) == 1
        width, height = document[0].get_size()
        # 40x30 px with no DPI metadata maps to 40x30 pt at the default scale.
        assert abs(width - 40) <= 1 and abs(height - 30) <= 1
    finally:
        document.close()