import asyncio

import pytest

import src.application.services.conversion_service as conversion_service_module
from src.application.services.conversion_service import ConversionService
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType


def test_submit_conversion_job_successfully_enqueues_job(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    returned_id = asyncio.run(service.push_conversion_job(conversion_job))

    assert returned_id == conversion_job.job_id
    assert fake_queue_port.pushed_jobs == [conversion_job]


def test_submit_conversion_job_rejects_unsupported_conversion(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    with pytest.raises(InvalidConversion):
        asyncio.run(service.push_conversion_job(conversion_job))

    assert fake_queue_port.pushed_jobs == []


def test_push_transitions_job_to_pending_and_persists(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushing an AWAITING_UPLOAD job moves it to PENDING and stores it."""
    from src.domain.conversions.value_object.job_status import JobStatus

    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    asyncio.run(service.push_conversion_job(conversion_job))

    assert conversion_job.status == JobStatus.PENDING
    assert fake_repository_port.job_table["job-1"].status == JobStatus.PENDING


def test_push_requires_job_id_before_publishing(
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job without an ID must be rejected before anything is enqueued."""
    from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
    from src.domain.conversions.entities.conversion_job import ConversionJob
    from src.domain.conversions.value_object.conversion_type import ConversionType

    job = ConversionJob(
        job_id="",
        conversion=ConversionType("txt", "md"),
        input_file="input.txt",
        object_key="uploads/input.txt",
    )

    @converter_registry.register(job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    with pytest.raises(InvalidConversionJobError):
        asyncio.run(service.push_conversion_job(job))

    assert fake_queue_port.pushed_jobs == []


def test_submit_conversion_job_calls_queue_exactly_once(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    asyncio.run(service.push_conversion_job(conversion_job))

    assert len(fake_queue_port.pending) == 1


def test_submit_conversion_job_returns_original_job_id(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @converter_registry.register(conversion_job.conversion)
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    assert asyncio.run(service.push_conversion_job(conversion_job)) == "job-1"


def test_submit_conversion_job_propagates_queue_failure(
    conversion_job,
    converter_registry,
    fake_repository_port,   
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingQueue:
        async def publish_job(self, job, stream: str | None = None):
            del job
            del stream
            raise RuntimeError("queue unavailable")

    @converter_registry.register(ConversionType(source_format="txt", target_format="md"))
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=FailingQueue(), db_repository=fake_repository_port)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        asyncio.run(service.push_conversion_job(conversion_job))


def test_retry_conversion_job_resets_failed_job_and_reenqueues(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
    from src.domain.conversions.value_object.job_status import JobStatus
    from src.domain.conversions.entities.conversion_job import ConversionJob

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    # Store a FAILED job with an object_key (as a real failed job would have).
    # The job is owned: retry requires that the caller owns it (a job with
    # user_id=None is a guest job and cannot be retried through this path).
    failed = ConversionJob(
        job_id=conversion_job.job_id,
        conversion=conversion_job.conversion,
        input_file=conversion_job.input_file,
        object_key="uploads/input.pdf",
        status=JobStatus.FAILED,
        error_message="boom",
        user_id=101,
    )
    asyncio.run(fake_repository_port.save_conversion_job(failed))
    fake_queue_port.pending.clear()

    returned = asyncio.run(service.retry_conversion_job(failed.job_id, failed.user_id))

    # The service returns the retried job directly (no extra DB fetch needed).
    assert returned is failed
    assert returned.status == JobStatus.PENDING
    assert returned.error_message is None
    assert returned.object_key == "uploads/input.pdf"
    # The stored job is reset to PENDING with error cleared and re-enqueued.
    stored = fake_repository_port.job_table[failed.job_id]
    assert stored.status == JobStatus.PENDING
    assert stored.error_message is None
    assert stored.object_key == "uploads/input.pdf"
    assert len(fake_queue_port.pushed_jobs) == 1
    assert fake_queue_port.pushed_jobs[0].object_key == "uploads/input.pdf"


def test_retry_conversion_job_raises_when_job_not_owned(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
    from src.domain.conversions.value_object.job_status import JobStatus
    from src.domain.conversions.entities.conversion_job import ConversionJob

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    failed = ConversionJob(
        job_id=conversion_job.job_id,
        conversion=conversion_job.conversion,
        input_file=conversion_job.input_file,
        object_key="uploads/input.pdf",
        status=JobStatus.FAILED,
        user_id=7,
    )
    asyncio.run(fake_repository_port.save_conversion_job(failed))

    with pytest.raises(InvalidConversionJobError, match="Job not found"):
        asyncio.run(service.retry_conversion_job(failed.job_id, user_id=999))


def test_retry_conversion_job_raises_when_job_missing(
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.application.exceptions.conversion_job_exception import InvalidConversionJobError

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    with pytest.raises(InvalidConversionJobError, match="Job not found"):
        asyncio.run(service.retry_conversion_job("missing", user_id=None))


def test_retry_conversion_job_raises_for_ownerless_guest_job(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEC-4/DEDUP-3: a guest job (``user_id=None``) must never be retryable.

    The old ``job.user_id is not None and ...`` guard made the ownership check
    vacuous for ownerless jobs; this pins the corrected, fail-closed rule.
    """
    from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
    from src.domain.conversions.value_object.job_status import JobStatus
    from src.domain.conversions.entities.conversion_job import ConversionJob

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    guest_job = ConversionJob(
        job_id="guest-job",
        conversion=conversion_job.conversion,
        input_file=conversion_job.input_file,
        object_key="uploads/guest.pdf",
        status=JobStatus.FAILED,
        user_id=None,
    )
    asyncio.run(fake_repository_port.save_conversion_job(guest_job))

    with pytest.raises(InvalidConversionJobError, match="Job not found"):
        asyncio.run(service.retry_conversion_job("guest-job", user_id=None))


def test_list_history_returns_only_users_jobs_paginated(
    conversion_job,
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.domain.conversions.entities.conversion_job import ConversionJob
    from src.domain.conversions.value_object.conversion_type import ConversionType
    from src.domain.conversions.value_object.job_status import JobStatus

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    own = ConversionJob(
        job_id="job-own", conversion=ConversionType("txt", "md"),
        input_file="a.txt", user_id=1, status=JobStatus.COMPLETED, output_file="a.md",
    )
    other = ConversionJob(
        job_id="job-other", conversion=ConversionType("txt", "md"),
        input_file="b.txt", user_id=2, status=JobStatus.COMPLETED, output_file="b.md",
    )
    asyncio.run(fake_repository_port.save_conversion_job(own))
    asyncio.run(fake_repository_port.save_conversion_job(other))

    rows, total = asyncio.run(service.list_history(1, offset=0, limit=20))

    assert total == 1
    assert [j.job_id for j in rows] == ["job-own"]

    rows_page2, total = asyncio.run(service.list_history(1, offset=10, limit=20))
    assert rows_page2 == []
    assert total == 1


def test_convert_library_file_creates_and_enqueues_job(
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """convert_library_file builds a job with object_key, persists it, and enqueues it."""
    from src.domain.conversions.value_object.job_status import JobStatus

    @converter_registry.register(ConversionType(source_format="pdf", target_format="docx"))
    def noop_converter(input_path: str, output_path: str) -> None:
        del input_path
        del output_path

    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    job = asyncio.run(service.convert_library_file(
        file_name="report.pdf",
        source_format="pdf",
        target_format="docx",
        object_key="uploads/report.pdf",
        user_id=42,
    ))

    assert job.job_id  # create_conversion_job assigns a UUID
    assert job.status == JobStatus.PENDING
    assert job.object_key == "uploads/report.pdf"
    assert job.input_file == "report.pdf"
    assert job.user_id == 42
    assert job.conversion.source_format == "pdf"
    assert job.conversion.target_format == "docx"
    # Both the DB record and the queue should reflect the enqueued job.
    stored = fake_repository_port.job_table[job.job_id]
    assert stored.status == JobStatus.PENDING
    assert fake_queue_port.pushed_jobs == [job]
    assert fake_queue_port.pushed_jobs[0].object_key == "uploads/report.pdf"


def test_convert_library_file_rejects_unsupported_conversion(
    fake_queue_port,
    fake_repository_port,
    converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported source/target combo must raise InvalidConversion and not enqueue."""
    monkeypatch.setattr(conversion_service_module, "get_registry", lambda: converter_registry)
    service = ConversionService(queue_port=fake_queue_port, db_repository=fake_repository_port)

    with pytest.raises(InvalidConversion):
        asyncio.run(service.convert_library_file(
            file_name="notes.xyz",
            source_format="xyz",
            target_format="docx",
            object_key="uploads/notes.xyz",
            user_id=42,
        ))

    assert fake_queue_port.pushed_jobs == []
    assert fake_repository_port.job_table == {}