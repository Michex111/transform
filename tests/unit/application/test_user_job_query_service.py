import asyncio
from datetime import UTC, datetime

from src.application.dtos.job_views import ActiveQueueItem, ConversionHistoryItem, Page
from src.application.services.user_job_query_service import UserJobQueryService


class StubReadRepository:
    async def list_user_history(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionHistoryItem], int]:
        del user_id
        del offset
        del limit
        now = datetime(2026, 7, 25, tzinfo=UTC)
        return (
            [
                ConversionHistoryItem(
                    job_id="job-1",
                    status="COMPLETED",
                    source_format="docx",
                    target_format="pdf",
                    input_file="uploads/in.docx",
                    output_file="outputs/out.pdf",
                    error_message=None,
                    created_at=now,
                    updated_at=now,
                ),
                ConversionHistoryItem(
                    job_id="job-2",
                    status="FAILED",
                    source_format="pdf",
                    target_format="docx",
                    input_file="uploads/in.pdf",
                    output_file=None,
                    error_message="conversion failed",
                    created_at=now,
                    updated_at=now,
                ),
            ],
            2,
        )

    async def list_user_active_jobs(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ActiveQueueItem], int]:
        del user_id
        del offset
        del limit
        return (
            [
                ActiveQueueItem(
                    job_id="job-3",
                    status="PENDING",
                    source_format="docx",
                    target_format="pdf",
                    input_file="uploads/waiting.docx",
                    created_at=datetime(2026, 7, 25, tzinfo=UTC),
                )
            ],
            1,
        )


class StubMinioGateway:
    def __init__(self) -> None:
        self.requested_keys: list[str] = []

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        del expires_in_minutes
        self.requested_keys.append(object_key)
        return f"https://download.test/{object_key}"


def test_list_history_resolves_download_urls_only_for_completed_outputs() -> None:
    object_gateway = StubMinioGateway()
    service = UserJobQueryService(
        repository=StubReadRepository(),
        object_gateway=object_gateway,
    )

    result = asyncio.run(service.list_history("1", Page(page=1, page_size=20)))

    assert result.total == 2
    assert result.items[0].download_url == "https://download.test/outputs/out.pdf"
    assert result.items[1].download_url is None
    assert object_gateway.requested_keys == ["outputs/out.pdf"]


def test_list_active_queue_returns_pending_and_processing_jobs() -> None:
    service = UserJobQueryService(
        repository=StubReadRepository(),
        object_gateway=StubMinioGateway(),
    )

    result = asyncio.run(service.list_active_queue("1", Page(page=1, page_size=20)))

    assert result.total == 1
    assert result.items[0].job_id == "job-3"
    assert result.items[0].status == "PENDING"
