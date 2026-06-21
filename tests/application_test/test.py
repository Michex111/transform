import asyncio

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.entities.conversion_job import ConversionJob
from src.domain.exceptions import InvalidConversion
from src.domain.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import ConverterRegistry


class InMemoryQueue:
    def __init__(self):
        self.pending: list[tuple[str, ConversionJob]] = []
        self.sequence = 0

    async def push_job(self, job: ConversionJob) -> None:
        self.sequence += 1
        self.pending.append((f"message-{self.sequence}", job))


def test_submit_conversion_job_queues_supported_job(monkeypatch: pytest.MonkeyPatch):
    queue = InMemoryQueue()
    service = ConversionService(queue_port=queue)
    conversion = ConversionType(source_format="txt", target_format="md")

    registry = ConverterRegistry()

    @registry.register(conversion)
    def passthrough_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: registry)

    job = ConversionJob(
        job_id="job-1",
        conversion=conversion,
        input_file="s3-file_store/input.txt",
    )

    returned_id = asyncio.run(service.submit_conversion_job(job))

    assert returned_id == "job-1"
    assert len(queue.pending) == 1
    message_id, queued_job = queue.pending[0]
    assert message_id == "message-1"
    assert queued_job.job_id == "job-1"


def test_submit_conversion_job_rejects_unsupported_conversion(monkeypatch: pytest.MonkeyPatch):
    queue = InMemoryQueue()
    service = ConversionService(queue_port=queue)

    registry = ConverterRegistry()
    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: registry)

    unsupported_job = ConversionJob(
        job_id="job-2",
        conversion=ConversionType(source_format="xlsx", target_format="pdf"),
        input_file="s3-file_store/spreadsheet.xlsx",
    )

    with pytest.raises(InvalidConversion):
        asyncio.run(service.submit_conversion_job(unsupported_job))

    assert queue.pending == []