import asyncio

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.exceptions import InvalidConversion
from src.domain.value_object.conversion_type import ConversionType


def test_submit_conversion_job_successfully_enqueues_job(
    conversion_job,
    fake_queue_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port)

    returned_id = asyncio.run(service.submit_conversion_job(conversion_job))

    assert returned_id == conversion_job.job_id
    assert fake_queue_port.pushed_jobs == [conversion_job]


def test_submit_conversion_job_rejects_unsupported_conversion(
    conversion_job,
    fake_queue_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port)

    with pytest.raises(InvalidConversion):
        asyncio.run(service.submit_conversion_job(conversion_job))

    assert fake_queue_port.pushed_jobs == []


def test_submit_conversion_job_calls_queue_exactly_once(
    conversion_job,
    fake_queue_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port)

    asyncio.run(service.submit_conversion_job(conversion_job))

    assert len(fake_queue_port.pending) == 1


def test_submit_conversion_job_returns_original_job_id(
    conversion_job,
    fake_queue_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port)

    assert asyncio.run(service.submit_conversion_job(conversion_job)) == "job-1"


def test_submit_conversion_job_propagates_queue_failure(
    conversion_job,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingQueue:
        async def push_job(self, job):
            del job
            raise RuntimeError("queue unavailable")

    @converter_registry.register(ConversionType(source_format="txt", target_format="md"))
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=FailingQueue())

    with pytest.raises(RuntimeError, match="queue unavailable"):
        asyncio.run(service.submit_conversion_job(conversion_job))