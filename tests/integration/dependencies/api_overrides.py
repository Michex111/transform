from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from fastapi.testclient import TestClient

import src.presentation.api.main as api_main
from src.application.dtos.subscription_dto import ConversionAuthorization
from src.application.dtos.upload_dto import UploadResponse
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_access_service,
    get_subscription_repository,
    get_conversion_service,
    get_transfer_service,
)


class FakeConversionService:
    def __init__(self) -> None:
        self.created_jobs: list[ConversionJob] = []

    async def create_conversion_job(self, job: ConversionJob) -> str:
        job.job_id = "test-job-id"
        self.created_jobs.append(job)
        return job.job_id


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


class FakeConversionAccessService:
    async def authorize_conversion(self, actor, incoming_file_size_bytes: int):
        del incoming_file_size_bytes
        return ConversionAuthorization(
            actor=actor,
            period_key="2026-07",
            credits_remaining=99,
            queue_stream="conversion_jobs:normal",
        )


class FakeSubscriptionRepository:
    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        del actor_key
        return SubscriptionTier.FREE


@dataclass
class FakeUser:
    id: int


@contextmanager
def create_test_client() -> Generator[TestClient, None, None]:
    async def no_op_initialize_database() -> None:
        return None

    fake_conversion_service = FakeConversionService()
    fake_transfer_service = FakeTransferService()
    fake_conversion_access_service = FakeConversionAccessService()
    fake_subscription_repository = FakeSubscriptionRepository()
    original_initialize_database = api_main.initialize_database

    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=101)
    api_main.app.dependency_overrides[get_conversion_service] = lambda: fake_conversion_service
    api_main.app.dependency_overrides[get_transfer_service] = lambda: fake_transfer_service
    api_main.app.dependency_overrides[get_conversion_access_service] = (
        lambda: fake_conversion_access_service
    )
    api_main.app.dependency_overrides[get_subscription_repository] = (
        lambda: fake_subscription_repository
    )
    api_main.app.state.fake_conversion_service = fake_conversion_service
    api_main.app.state.fake_transfer_service = fake_transfer_service

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
