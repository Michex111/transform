from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from fastapi.testclient import TestClient

import src.presentation.api.main as api_main
from src.application.dtos.upload_dto import UploadResponse
from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserFileModel
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_service,
    get_file_service,
    get_transfer_service,
)


class FakeConversionService:
    def __init__(self) -> None:
        self.created_jobs: list[ConversionJob] = []

    async def create_conversion_job(self, job: ConversionJob) -> str:
        job.job_id = "test-job-id"
        self.created_jobs.append(job)
        return job.job_id

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: "SubscriptionTier | None" = None,
    ) -> ConversionJob:
        del tier
        # Validate against the real registry so the route's InvalidConversion
        # -> 400 mapping is exercised for unsupported source/target formats.
        from src.domain.conversions.policies.conversion_policy import is_supported
        from src.infrastructure.converters.converter_registry import get_registry

        job = ConversionJob(
            job_id="test-job-id",
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=file_name,
            object_key=object_key,
            user_id=user_id,
        )
        is_supported(job.conversion, get_registry().list_conversions())
        job.pending_processing()
        self.created_jobs.append(job)
        return job

    async def update_conversion_job(self, job: ConversionJob) -> None:
        del job  # object_key is already persisted on the in-memory object

    async def retry_conversion_job(
        self,
        job_id: str,
        user_id: int | None,
        tier: "SubscriptionTier | None" = None,
    ) -> ConversionJob:
        del user_id
        del tier
        for job in self.created_jobs:
            if job.job_id == job_id:
                job.retry()
                return job
        raise InvalidConversionJobError("Job not found")

    async def list_history(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        since=None,
    ) -> tuple[list[ConversionJob], int]:
        del user_id
        del since
        rows = self.created_jobs[offset:offset + limit]
        return rows, len(self.created_jobs)


class FakeFileService:
    def __init__(self) -> None:
        self.files: dict[str, UserFileModel] = {}

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        row = self.files.get(file_id)
        if row is None or row.user_id != user_id:
            raise FileRecordNotFoundError()
        return row


class FakeTransferService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def create_upload(self, file_extension: str, user_id: str) -> UploadResponse:
        self.calls.append((file_extension, user_id))
        return UploadResponse(
            upload_id="upload-session-id",
            object_key="uploads/example.docx",
            upload_url="https://storage.test/upload-url",
            expires_in_minutes=10,
        )


@dataclass
class FakeUser:
    id: int


@contextmanager
def create_test_client() -> Generator[TestClient, None, None]:
    async def no_op_initialize_database() -> None:
        return None

    fake_conversion_service = FakeConversionService()
    fake_transfer_service = FakeTransferService()
    fake_file_service = FakeFileService()
    original_initialize_database = api_main.initialize_database

    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=101)
    api_main.app.dependency_overrides[get_conversion_service] = lambda: fake_conversion_service
    api_main.app.dependency_overrides[get_transfer_service] = lambda: fake_transfer_service
    api_main.app.dependency_overrides[get_file_service] = lambda: fake_file_service
    api_main.app.state.fake_conversion_service = fake_conversion_service
    api_main.app.state.fake_transfer_service = fake_transfer_service
    api_main.app.state.fake_file_service = fake_file_service

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_initialize_database
        if hasattr(api_main.app.state, "fake_conversion_service"):
            delattr(api_main.app.state, "fake_conversion_service")
        if hasattr(api_main.app.state, "fake_transfer_service"):
            delattr(api_main.app.state, "fake_transfer_service")
        if hasattr(api_main.app.state, "fake_file_service"):
            delattr(api_main.app.state, "fake_file_service")
