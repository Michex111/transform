import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import workers.converter_workers.processor as processor_module
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from tests.fakes.fake_credit_port import FakeCreditPort
from tests.fakes.fake_logger import FakeLogger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import _convert_file, process_job, resolve_path


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


def test_resolve_path_truncates_very_long_filenames() -> None:
    """A long source name must not exceed the OS 255-char filename limit once
    the Minio `.part.minio` suffix / encryption `plain_` prefix is added."""
    long_name = "Architecture Patterns with Python " * 8 + ".pdf"  # ~300 chars
    input_file, output_file = resolve_path(
        f"s3-file_store/{long_name}",
        ConversionType(source_format="pdf", target_format="docx"),
        Path("/tmp/work"),
    )

    assert len(input_file.name) <= 200
    assert len(output_file.name) <= 200
    # Extension must be preserved so the converter infers the right source.
    assert input_file.name.endswith(".pdf")
    assert output_file.name.endswith(".docx")


def test_convert_file_times_out_a_hung_converter(
    conversion_job,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A converter that never returns must abort via WORKER_CONVERSION_TIMEOUT."""
    import asyncio

    import workers.converter_workers.processor as processor_module

    # Simulate a converter that takes longer than the configured timeout. Note:
    # asyncio.wait_for cannot kill the underlying worker thread, so the thread
    # still completes its sleep; we use a short sleep so the test is fast.
    import time

    @fake_converter_registry.register(conversion_job.conversion)
    def slow_converter(input_path: str, output_path: str) -> None:
        del input_path, output_path
        time.sleep(2)

    monkeypatch.setattr(processor_module.settings, "WORKER_CONVERSION_TIMEOUT", 0.1)

    context = WorkerContext(
        storage_port=None,  # type: ignore[arg-type]
        queue_port=None,    # type: ignore[arg-type]
        event_port=None,    # type: ignore[arg-type]
        converter_registry=fake_converter_registry,
        worker_name="timeout-test",
    )
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_convert_file(context, conversion_job, Path("/tmp/in.pdf"), Path("/tmp/out.pdf")))


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

    conversion_job.pending_processing()
    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert conversion_job.output_file == "output/user/guest/job/job-1/input.md"
    assert fake_storage_port.objects["output/user/guest/job/job-1/input.md"] == b"HELLO WORLD"
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

    conversion_job.pending_processing()
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

    conversion_job.pending_processing()
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

    conversion_job.pending_processing()
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

    conversion_job.pending_processing()
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

    conversion_job.pending_processing()
    asyncio.run(process_job(context, conversion_job))

    assert any(
        level == "info" and "Starting processing job" in message
        for level, message, _ in fake_logger.records
    )


def test_process_job_with_encryption_stores_only_ciphertext(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """With at-rest encryption, input is decrypted for conversion and the
    uploaded output is ciphertext that the owner can decrypt."""
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    ciphertext = service.encrypt_bytes(b"hello world", "42")
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": ciphertext})
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    context = WorkerContext(
        storage_port=storage,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
        encryption_service=service,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    stored = storage.objects["output/user/42/job/job-1/input.md"]
    # At rest it must be ciphertext, not the converted plaintext
    assert stored != b"HELLO WORLD"
    assert stored.startswith(b"TRENC")
    # The owner can decrypt it back to the converted content
    assert service.decrypt_bytes(stored, "42") == b"HELLO WORLD"


def test_process_job_encryption_guest_job_uses_guest_key(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """Jobs without a user id still get encrypted (derived from the 'guest' key)."""
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    ciphertext = service.encrypt_bytes(b"guest data", "guest")
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": ciphertext})
    conversion_job.user_id = None
    conversion_job.pending_processing()

    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    context = WorkerContext(
        storage_port=storage,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
        encryption_service=service,
    )

    asyncio.run(process_job(context, conversion_job))

    stored = storage.objects["output/user/guest/job/job-1/input.md"]
    assert stored.startswith(b"TRENC")
    assert service.decrypt_bytes(stored, "guest") == b"GUEST DATA"


def test_process_job_without_encryption_stores_plaintext(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """Without a configured encryption service the pipeline stays plaintext."""
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
        encryption_service=None,
    )

    conversion_job.pending_processing()
    asyncio.run(process_job(context, conversion_job))

    assert fake_storage_port.objects["output/user/guest/job/job-1/input.md"] == b"HELLO WORLD"


def _register_uppercase_converter(conversion_job, fake_converter_registry):
    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        text = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(text.upper(), encoding="utf-8")

    return converter


def _build_context(
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    credit_port=None,
    **kwargs,
) -> WorkerContext:
    return WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
        credit_port=credit_port,
        **kwargs,
    )


def test_process_job_exhausted_credits_fails_without_converting(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """remaining=0 -> FAILED, no download/convert, terminal event, no retry (no raise)."""
    credit_port = FakeCreditPort(remaining=0)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    def converter(input_path: str, output_path: str) -> None:
        raise AssertionError("converter must not be invoked when credits are exhausted")

    fake_converter_registry.register(conversion_job.conversion)(converter)

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
    )

    # Must NOT raise: exhausted credits are a permanent, acked state.
    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.FAILED
    assert conversion_job.error_message == (
        "Conversion credits exhausted. Upgrade your plan or purchase more credits."
    )
    assert fake_storage_port.download_calls == []  # no heavy work performed
    assert credit_port.consume_calls == []  # nothing deducted

    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.FAILED
    assert terminal["progress"] == 100
    assert "credits exhausted" in terminal["message"]
    assert terminal["credits_remaining"] == 0


def test_process_job_sufficient_credits_consumes_and_completes(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    credit_port = FakeCreditPort(remaining=50, tier=SubscriptionTier.PREMIUM)
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert len(credit_port.consume_calls) == 1
    user_id, period_key, units = credit_port.consume_calls[0]
    assert user_id == 42
    assert period_key == datetime.now(UTC).strftime("%Y-%m")
    assert units == conversion_job.credits_used

    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.COMPLETED
    assert terminal["credits_used"] == conversion_job.credits_used
    assert terminal["credits_remaining"] == 50 - conversion_job.credits_used


def test_process_job_without_credit_port_completes_as_before(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """No credit port wired -> identical to today: completes, no consume, no balance event."""
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=None,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert fake_storage_port.objects["output/user/42/job/job-1/input.md"] == b"HELLO WORLD"
    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.COMPLETED
    assert "credits_remaining" not in terminal


def test_process_job_guest_skips_credit_logic_even_when_port_exhausted(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """Guest job with a credit port still converts normally (no pre-check, no deduction)."""
    credit_port = FakeCreditPort(remaining=0)
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = None
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert credit_port.consume_calls == []  # no deduction for guests
    assert fake_storage_port.objects["output/user/guest/job/job-1/input.md"] == b"HELLO WORLD"
    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.COMPLETED
    assert "credits_remaining" not in terminal


def test_process_job_consume_failure_still_completes(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """consume() raising must not fail the conversion — user keeps their output."""
    logger = FakeLogger()
    monkeypatch.setattr(processor_module, "worker_logger", logger)
    credit_port = FakeCreditPort(remaining=50)
    credit_port.fail_consume = True
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert fake_storage_port.objects["output/user/42/job/job-1/input.md"] == b"HELLO WORLD"
    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.COMPLETED
    assert "credits_remaining" not in terminal  # deduction failed -> no balance to report
    assert any(level == "warning" and "consume" in message for level, message, _ in logger.records)


def test_process_job_credit_precheck_failure_proceeds_best_effort(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_remaining/get_tier outage is non-fatal: convert anyway and deduct after."""
    logger = FakeLogger()
    monkeypatch.setattr(processor_module, "worker_logger", logger)
    credit_port = FakeCreditPort(remaining=50)
    credit_port.fail_get_remaining = True
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert fake_storage_port.objects["output/user/42/job/job-1/input.md"] == b"HELLO WORLD"
    assert len(credit_port.consume_calls) == 1  # still deducted after successful conversion
    assert any(level == "warning" and "pre-check" in message for level, message, _ in logger.records)