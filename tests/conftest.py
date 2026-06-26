import os
from pathlib import Path

import pytest

from src.infrastructure.converters.converter_registry import ConverterRegistry
from tests.fakes.fake_converter_registry import FakeConverterRegistry
from tests.fakes.fake_event_publisher import FakeEventPublisher
from tests.fakes.fake_logger import FakeLogger
from tests.fakes.fake_queue import FakeQueuePort
from tests.fakes.fake_storage import FakeStoragePort
from workers.converter_workers.context.worker_context import WorkerContext


# Keep worker processor imports deterministic in tests by ensuring required env vars exist.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BACKBLAZE_ENDPOINT", "https://example.invalid")
os.environ.setdefault("BACKBLAZE_ACCESS_KEY", "dummy-access-key")
os.environ.setdefault("BACKBLAZE_SECRET_KEY", "dummy-secret-key")
os.environ.setdefault("BASE_TARGET_KEY", "s3-file_store")


pytest_plugins = [
    "tests.fixtures.jobs",
    "tests.fixtures.events",
    "tests.fixtures.converters",
    "tests.fixtures.files",
]


@pytest.fixture
def fake_queue_port() -> FakeQueuePort:
    return FakeQueuePort()


@pytest.fixture
def fake_storage_port() -> FakeStoragePort:
    return FakeStoragePort(seed_files={"s3-file_store/input.txt": b"hello world"})


@pytest.fixture
def fake_event_publisher() -> FakeEventPublisher:
    return FakeEventPublisher()


@pytest.fixture
def fake_converter_registry() -> FakeConverterRegistry:
    return FakeConverterRegistry()


@pytest.fixture
def logger_fixture() -> FakeLogger:
    return FakeLogger()


@pytest.fixture
def worker_context(
    fake_storage_port: FakeStoragePort,
    fake_queue_port: FakeQueuePort,
    fake_event_publisher: FakeEventPublisher,
    converter_registry: ConverterRegistry,
) -> WorkerContext:
    return WorkerContext(
        storage_port=fake_storage_port,
        queue_port=fake_queue_port,
        event_port=fake_event_publisher,
        converter_registry=converter_registry,
        worker_name="pytest-worker",
    )


@pytest.fixture
def fake_settings() -> dict[str, str]:
    return {
        "REDIS_URL": os.environ["REDIS_URL"],
        "BACKBLAZE_ENDPOINT": os.environ["BACKBLAZE_ENDPOINT"],
        "BACKBLAZE_ACCESS_KEY": os.environ["BACKBLAZE_ACCESS_KEY"],
        "BACKBLAZE_SECRET_KEY": os.environ["BACKBLAZE_SECRET_KEY"],
        "BASE_TARGET_KEY": os.environ["BASE_TARGET_KEY"],
    }


@pytest.fixture
def sample_document_paths(tmp_path: Path) -> dict[str, Path]:
    input_path = tmp_path / "sample-input.txt"
    output_path = tmp_path / "sample-output.md"
    input_path.write_text("fixture-content", encoding="utf-8")
    return {"input": input_path, "output": output_path}