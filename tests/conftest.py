import os
from pathlib import Path

# IMPORTANT: Set the required environment variables BEFORE importing any module
# that calls get_settings() at import time (e.g. src.infrastructure.redis.client
# builds its client with get_settings()). Otherwise settings get cached from the
# .env file (e.g. BASE_TARGET_KEY=output/) instead of these test defaults, which
# breaks worker/processor tests that assert on the storage key.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BACKBLAZE_ENDPOINT", "https://example.invalid")
os.environ.setdefault("BACKBLAZE_ACCESS_KEY", "dummy-access-key")
os.environ.setdefault("BACKBLAZE_SECRET_KEY", "dummy-secret-key")
os.environ.setdefault("BASE_TARGET_KEY", "output/")

# CORS: the SPA is now hosted on its own origin, so it must be allow-listed
# BEFORE src.presentation.api.main is imported (the app builds its
# CORSMiddleware at import time from get_settings()). Without this the
# cross-origin tests only passed when the developer's gitignored .env happened
# to define ALLOWED_ORIGINS; in CI the unset setting defaults to [] and the
# preflight is rejected. Set unconditionally so the suite is deterministic.
os.environ.setdefault(
    "ALLOWED_ORIGINS",
    '["http://localhost:5173","https://transform-web.onrender.com"]',
)
os.environ.setdefault(
    "S3_CORS_ALLOWED_ORIGINS",
    '["http://localhost:5173","https://transform-web.onrender.com"]',
)

import pytest  # noqa: E402

from src.infrastructure.converters.converter_registry import ConverterRegistry  # noqa: E402
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware  # noqa: E402
from tests.fakes.fake_converter_registry import FakeConverterRegistry  # noqa: E402
from tests.fakes.fake_event_publisher import FakeEventPublisher  # noqa: E402
from tests.fakes.fake_logger import FakeLogger  # noqa: E402
from tests.fakes.fake_queue import FakeQueuePort  # noqa: E402
from tests.fakes.fake_storage import FakeStoragePort  # noqa: E402
from tests.fakes.fake_db_repository import FakeDatabaseRepository  # noqa: E402
from workers.converter_workers.context.worker_context import WorkerContext  # noqa: E402


pytest_plugins = [
    "tests.fixtures.jobs",
    "tests.fixtures.events",
    "tests.fixtures.converters",
    "tests.fixtures.files",
]


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Reset the shared rate limiter (in-memory and Redis-backed) so per-IP
    counters do not accumulate across test cases (prevents spurious 429s)."""
    RateLimitMiddleware.reset_all()
    yield
    RateLimitMiddleware.reset_all()


@pytest.fixture
def fake_queue_port() -> FakeQueuePort:
    return FakeQueuePort()


@pytest.fixture
def fake_storage_port() -> FakeStoragePort:
    return FakeStoragePort(seed_files={"s3-file_store/input.txt": b"hello world"})

@pytest.fixture
def fake_repository_port() -> FakeDatabaseRepository:
    return FakeDatabaseRepository()


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