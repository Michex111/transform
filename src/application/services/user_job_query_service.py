from src.application.dtos.job_views import ActiveQueueItem, ConversionHistoryItem, Page, PaginatedResult
from src.application.ports.database_port import ConversionJobReadRepository
from src.application.ports.minio_port import MinioObjectGateway


class UserJobQueryService:
    """Provides user history and active queue views."""

    def __init__(
        self,
        repository: ConversionJobReadRepository,
        object_gateway: MinioObjectGateway,
        download_url_ttl_minutes: int = 15,
    ) -> None:
        self._repository = repository
        self._object_gateway = object_gateway
        self._download_url_ttl_minutes = download_url_ttl_minutes

    async def list_history(self, user_id: str, page: Page) -> PaginatedResult[ConversionHistoryItem]:
        """Returns paginated history with signed download URLs for completed outputs."""
        items, total = await self._repository.list_user_history(
            user_id=user_id,
            offset=page.offset,
            limit=page.page_size,
        )
        resolved: list[ConversionHistoryItem] = []
        for item in items:
            download_url = None
            if item.status == "COMPLETED" and item.output_file:
                download_url = self._object_gateway.generate_get_url(
                    object_key=item.output_file,
                    expires_in_minutes=self._download_url_ttl_minutes,
                )
            resolved.append(
                ConversionHistoryItem(
                    job_id=item.job_id,
                    status=item.status,
                    source_format=item.source_format,
                    target_format=item.target_format,
                    input_file=item.input_file,
                    output_file=item.output_file,
                    error_message=item.error_message,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    download_url=download_url,
                )
            )
        return PaginatedResult(
            items=resolved,
            total=total,
            page=page.page,
            page_size=page.page_size,
        )

    async def list_active_queue(self, user_id: str, page: Page) -> PaginatedResult[ActiveQueueItem]:
        """Returns paginated list of pending and processing jobs."""
        items, total = await self._repository.list_user_active_jobs(
            user_id=user_id,
            offset=page.offset,
            limit=page.page_size,
        )
        return PaginatedResult(
            items=items,
            total=total,
            page=page.page,
            page_size=page.page_size,
        )
