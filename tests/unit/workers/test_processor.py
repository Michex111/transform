import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

import workers.converter_workers.processor as processor_module
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from tests.fakes.fake_credit_port import FakeCreditPort
from tests.fakes.fake_db_repository import FakeDatabaseRepository
from tests.fakes.fake_logger import FakeLogger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import (
    _convert_file,
    _download_input_file,
    _upload_output_file,
    process_job,
    resolve_path,
)
from workers.converter_workers.processor import resolve_input_path, resolve_output_path


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


def test_resolve_input_path_builds_the_download_path() -> None:
    input_file = resolve_input_path("s3-file_store/manual.txt", Path("/tmp/work"))

    assert input_file.name == "manual.txt"
    assert input_file.parent.name == "downloads"


def test_resolve_output_path_defaults_to_the_target_format() -> None:
    output_file = resolve_output_path(
        "s3-file_store/manual.txt",
        ConversionType(source_format="txt", target_format="md"),
        Path("/tmp/work"),
    )

    assert output_file.name == "manual.md"
    assert output_file.parent.name == "uploads"


def test_resolve_output_path_uses_a_declared_container_extension() -> None:
    """A converter may declare that it writes a container instead of the
    target format (e.g. pdf -> png bundles pages into a .zip)."""

    def converter(input_path: str, output_path: str) -> None:
        path = Path(output_path)
        path.write_bytes(b"PK\x03\x04")

    converter.output_extension = "zip"  # type: ignore[attr-defined]

    output_file = resolve_output_path(
        "s3-file_store/manual.pdf",
        ConversionType(source_format="pdf", target_format="png"),
        Path("/tmp/work"),
        converter=converter,
        input_path="/tmp/work/downloads/manual.pdf",
    )

    assert output_file.name == "manual.zip"


def test_resolve_output_path_uses_a_callable_extension_hook() -> None:
    """The hook may derive the extension from the downloaded input."""

    def converter(input_path: str, output_path: str) -> None:
        path = Path(output_path)
        path.write_bytes(b"PK\x03\x04")

    def hook(input_path: str) -> str | None:
        return "zip" if Path(input_path).name == "multi.pdf" else None

    converter.output_extension = hook  # type: ignore[attr-defined]
    conversion = ConversionType(source_format="pdf", target_format="png")

    multi = resolve_output_path(
        "s3-file_store/multi.pdf",
        conversion,
        Path("/tmp/work"),
        converter=converter,
        input_path="/tmp/work/downloads/multi.pdf",
    )
    single = resolve_output_path(
        "s3-file_store/single.pdf",
        conversion,
        Path("/tmp/work"),
        converter=converter,
        input_path="/tmp/work/downloads/single.pdf",
    )

    assert multi.name == "multi.zip"
    # A hook that declines keeps the target format's extension.
    assert single.name == "single.png"


def test_resolve_output_path_without_a_hook_argument_keeps_the_target() -> None:
    """A callable hook that needs the input falls back when it has none."""

    def converter(input_path: str, output_path: str) -> None:
        path = Path(output_path)
        path.write_bytes(b"PK\x03\x04")

    converter.output_extension = lambda input_path: "zip"  # type: ignore[attr-defined]

    output_file = resolve_output_path(
        "s3-file_store/manual.pdf",
        ConversionType(source_format="pdf", target_format="png"),
        Path("/tmp/work"),
        converter=converter,
    )

    assert output_file.name == "manual.png"


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

    # The sizes the detail panel reports: the plaintext input the converter read
    # and the file it produced. The fake converter upper-cases "hello world", so
    # both are 11 bytes here -- what matters is that they were measured at all
    # and that they are non-zero (0 means "not measured", which the UI omits).
    assert conversion_job.input_size_bytes == len(b"hello world")
    assert conversion_job.output_size_bytes == len(b"HELLO WORLD")


def test_process_job_reports_file_sizes_in_the_terminal_event(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """The completion event carries the byte sizes, so the panel is complete
    without waiting for a history refresh."""

    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        # A conversion that changes size, so the two values cannot be confused.
        Path(output_path).write_text("a much longer output than the input", encoding="utf-8")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    conversion_job.pending_processing()
    asyncio.run(process_job(context, conversion_job))

    completed = [
        e for e in fake_event_publisher.published_events if e.get("status") == "COMPLETED"
    ]
    assert len(completed) == 1
    assert completed[0]["input_size_bytes"] == len(b"hello world")
    assert completed[0]["output_size_bytes"] == len(b"a much longer output than the input")
    # The two are genuinely different, so a swap between them would fail here.
    assert completed[0]["input_size_bytes"] != completed[0]["output_size_bytes"]


def test_process_job_uploads_a_container_under_its_declared_extension(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """A converter that bundles several files (pdf -> png pages) declares the
    container extension, so the stored object is a .zip rather than an image."""
    seen_input_paths: list[str] = []

    def hook(input_path: str) -> str | None:
        seen_input_paths.append(input_path)
        return "zip"

    @fake_converter_registry.register(conversion_job.conversion)
    def converter(input_path: str, output_path: str) -> None:
        Path(output_path).write_bytes(b"PK\x03\x04zip of pages")

    converter.output_extension = hook  # type: ignore[attr-defined]

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=fake_converter_registry,
        worker_name="processor-test",
    )

    conversion_job.pending_processing()
    asyncio.run(process_job(context, conversion_job))

    expected_key = "output/user/guest/job/job-1/input.zip"
    assert conversion_job.status == JobStatus.COMPLETED
    assert conversion_job.output_file == expected_key
    assert fake_storage_port.objects[expected_key] == b"PK\x03\x04zip of pages"
    # The hook receives the downloaded plaintext input, not the object key.
    assert len(seen_input_paths) == 1
    assert Path(seen_input_paths[0]).name == "input.txt"


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


def test_process_job_passes_plaintext_input_through_when_encryption_enabled(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """A plaintext (direct browser) upload must convert even with encryption on.

    Regression test: uploads are written to object storage plaintext (no
    ingest-time encryption), so when a master key is configured the worker must
    NOT try to decrypt them. Previously it unconditionally called
    ``decrypt_file_to`` on plaintext input, raising
    ``Not a Transform-encrypted file (bad header)`` and failing the job — which
    is the 0-byte-file symptom reported by users.
    """
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    # Simulate a real browser upload: plaintext content stored in the bucket.
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": b"hello world"})
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

    # The job must complete with non-empty, correct output rather than failing.
    assert conversion_job.status == JobStatus.COMPLETED
    stored = storage.objects["output/user/42/job/job-1/input.md"]
    assert stored != b""
    # The output is re-encrypted at rest; the owner can decrypt it back.
    assert stored.startswith(b"TRENC")
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


def _build_fencr_blob(plaintext: bytes, data_key: bytes, chunk_size: int = 16) -> bytes:
    """Build a FENCR v1 blob in Python (mirroring the browser WebCrypto format)."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    salt = bytes(range(1, 17))
    nonce_prefix = bytes([0xAA]) * 8
    file_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"transform-client-v1:enc",
    ).derive(data_key)
    cipher = AESGCM(file_key)
    aad = b"transform-client-v1"
    header = b"FENCR" + b"\x01" + chunk_size.to_bytes(4, "big") + salt + nonce_prefix
    body = b""
    counter = 0
    for i in range(0, len(plaintext), chunk_size):
        chunk = plaintext[i:i + chunk_size]
        nonce = nonce_prefix + counter.to_bytes(4, "big")
        body += cipher.encrypt(nonce, chunk, aad)
        counter += 1
    return header + body


def test_process_job_decrypts_client_encrypted_fencr_input(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """A client-encrypted (FENCR) input must be decrypted before conversion.

    The browser encrypts the file into a FENCR blob and uploads it; the job
    carries the wrapped data key. The worker unwraps it and feeds the plaintext
    to the converter. The converted output is re-encrypted at rest (TRENC).
    """
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    data_key = bytes([0x42]) * 32

    # Build a client-encrypted FENCR blob and store it as the uploaded object.
    fencr_blob = _build_fencr_blob(b"hello world", data_key)
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": fencr_blob})

    conversion_job.user_id = 42
    conversion_job.client_encrypted = True
    conversion_job.data_key_wrapped = service.encrypt_file(data_key, "42").hex()
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
    # At rest the converted output is TRENC ciphertext; owner can decrypt it.
    stored = storage.objects["output/user/42/job/job-1/input.md"]
    assert stored.startswith(b"TRENC")
    assert service.decrypt_bytes(stored, "42") == b"HELLO WORLD"


def test_process_job_client_encrypted_guest_uses_guest_actor(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """Guest client-encrypted jobs unwrap the key under the fixed 'guest' actor."""
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    data_key = bytes([0x42]) * 32

    fencr_blob = _build_fencr_blob(b"guest data", data_key)
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": fencr_blob})

    conversion_job.user_id = None
    conversion_job.client_encrypted = True
    conversion_job.data_key_wrapped = service.encrypt_file(data_key, "guest").hex()
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
    stored = storage.objects["output/user/guest/job/job-1/input.md"]
    assert stored.startswith(b"TRENC")
    assert service.decrypt_bytes(stored, "guest") == b"GUEST DATA"


def test_process_job_client_encrypted_without_key_fails(
    conversion_job,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
) -> None:
    """A client-encrypted job missing its wrapped data key must fail cleanly."""
    from src.infrastructure.adapters.security.encryption import FileEncryptionService
    from tests.fakes.fake_storage import FakeStoragePort

    service = FileEncryptionService(allow_autogenerated_key=True)
    data_key = bytes([0x42]) * 32
    fencr_blob = _build_fencr_blob(b"hello world", data_key)
    storage = FakeStoragePort(seed_files={"s3-file_store/input.txt": fencr_blob})

    conversion_job.user_id = 42
    conversion_job.client_encrypted = True
    conversion_job.data_key_wrapped = None  # missing wrapped key
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

    with pytest.raises(RuntimeError, match="wrapped data key"):
        asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.FAILED


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


# ----------------------------------------------------------------------
# W-9 — idempotency guard against a redelivered job (double charge)
# ----------------------------------------------------------------------


def test_process_job_skips_a_job_already_completed_in_the_database(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    fake_repository_port,
) -> None:
    """A redelivered job must not be converted — or charged — twice.

    ``fetch_job`` rebuilds a fresh PENDING entity from the stream fields only,
    so without reading the persisted row a replay re-converts the file and calls
    ``consume`` a second time. The queue can legitimately redeliver (the stale
    reclaim re-queues a job whose worker crashed).
    """
    credit_port = FakeCreditPort(remaining=50)
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
        job_repository=fake_repository_port,
    )

    # First pass: converts, completes, persists the row, consumes credits once.
    asyncio.run(process_job(context, conversion_job))
    assert conversion_job.status == JobStatus.COMPLETED
    assert len(credit_port.consume_calls) == 1

    # A redelivery rebuilds a fresh PENDING entity for the same job id.
    redelivered = ConversionJob(
        job_id=conversion_job.job_id,
        conversion=conversion_job.conversion,
        input_file=conversion_job.input_file,
        object_key=conversion_job.object_key,
        user_id=42,
    )
    redelivered.pending_processing()
    downloads_before = len(fake_storage_port.download_calls)
    uploads_before = len(fake_storage_port.upload_calls)

    asyncio.run(process_job(context, redelivered))

    # Exactly ONE consume call in total, and no second conversion/upload.
    assert len(credit_port.consume_calls) == 1
    assert len(fake_storage_port.download_calls) == downloads_before
    assert len(fake_storage_port.upload_calls) == uploads_before
    # A terminal event is still emitted so a client waiting on SSE for this
    # delivery learns the job finished.
    terminal = fake_event_publisher.published_events[-1]
    assert terminal["status"] == JobStatus.COMPLETED
    assert terminal["progress"] == 100


def test_process_job_does_not_skip_a_job_that_has_no_completed_row(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    fake_repository_port,
) -> None:
    """The guard must not block a first-time run (nothing persisted yet)."""
    credit_port = FakeCreditPort(remaining=50)
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.user_id = 42
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        credit_port=credit_port,
        job_repository=fake_repository_port,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED
    assert len(credit_port.consume_calls) == 1
    assert len(fake_storage_port.download_calls) == 1


def test_process_job_proceeds_when_the_idempotency_read_fails(
    conversion_job,
    fake_storage_port,
    fake_queue_port,
    fake_event_publisher,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB hiccup on the guard read must not block a legitimate conversion."""
    repository = FakeDatabaseRepository()

    async def exploding_get(job_id: str):
        raise RuntimeError("db is down")

    monkeypatch.setattr(repository, "get_conversion_job", exploding_get)
    _register_uppercase_converter(conversion_job, fake_converter_registry)
    conversion_job.pending_processing()

    context = _build_context(
        fake_storage_port,
        fake_queue_port,
        fake_event_publisher,
        fake_converter_registry,
        job_repository=repository,
    )

    asyncio.run(process_job(context, conversion_job))

    assert conversion_job.status == JobStatus.COMPLETED


# ----------------------------------------------------------------------
# W-8 — object-storage calls must be bounded
# ----------------------------------------------------------------------


def test_hung_storage_transfer_fails_the_job_instead_of_hanging_the_worker(
    conversion_job,
    fake_storage_port,
    fake_converter_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled object-store socket must not park the worker loop forever.

    Storage calls are blocking and were previously unbounded: no other job ran,
    no stale sweep happened, and the container never exited so
    ``restart: unless-stopped`` never fired. ``asyncio.wait_for`` bounds the
    await (the underlying thread still runs — see the converter-timeout test).
    """
    import time as time_module

    def stalled_download(key: str, dest_path: Path) -> None:
        del key, dest_path
        time_module.sleep(1)

    monkeypatch.setattr(fake_storage_port, "download", stalled_download)
    monkeypatch.setattr(processor_module, "_STORAGE_OP_TIMEOUT_SECONDS", 0.05)

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=None,  # type: ignore[arg-type]
        event_port=None,  # type: ignore[arg-type]
        converter_registry=fake_converter_registry,
        worker_name="storage-timeout-test",
    )

    # ``_download_input_file`` is retried, so the final error is the timeout.
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            _download_input_file(context, conversion_job, Path("/tmp/in.txt"))
        )


def test_hung_storage_upload_fails_the_job_instead_of_hanging_the_worker(
    conversion_job,
    fake_storage_port,
    fake_converter_registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as time_module

    def stalled_upload(target_key: str, source_path: Path) -> None:
        del target_key, source_path
        time_module.sleep(1)

    monkeypatch.setattr(fake_storage_port, "upload", stalled_upload)
    monkeypatch.setattr(processor_module, "_STORAGE_OP_TIMEOUT_SECONDS", 0.05)

    output_file = tmp_path / "converted.md"
    output_file.write_text("done", encoding="utf-8")

    context = WorkerContext(
        storage_port=fake_storage_port,
        queue_port=None,  # type: ignore[arg-type]
        event_port=None,  # type: ignore[arg-type]
        converter_registry=fake_converter_registry,
        worker_name="storage-timeout-test",
    )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_upload_output_file(context, conversion_job, output_file))