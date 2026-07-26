from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.job_views import ActiveQueueItem, ConversionHistoryItem
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.database.models import ConversionJobModel

class SQLConversionJobRepository:
    """SQLAlchemy adapter for conversion job read/write use-cases."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """Saves a conversion job to the database.

        Args:
            job_data: Conversion job entity.
        """
        now = datetime.now(UTC)
        job_model = ConversionJobModel(
            job_id=job_data.job_id,
            status=job_data.status,
            source_format=job_data.conversion.source_format,
            target_format=job_data.conversion.target_format,
            input_file=job_data.input_file,
            output_file=job_data.output_file,
            error_message=job_data.error_message,
            user_id=getattr(job_data, "user_id", None),
            created_at=now,
            updated_at=now,
        )
        self.session.add(job_model)
        await self.session.commit()

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """Retrieves a conversion job by ID.

        Args:
            job_id: Conversion job identifier.

        Returns:
            Conversion job entity or None if not found.
        """
        stmt = select(ConversionJobModel).where(ConversionJobModel.job_id == job_id)
        result = await self.session.execute(stmt)
        job_model = result.scalar_one_or_none()

        if job_model is None:
            return None

        conversion_job = ConversionJob(
            job_id=job_model.job_id,
            conversion=ConversionType(job_model.source_format, job_model.target_format),
            input_file=job_model.input_file,
            output_file=job_model.output_file,
            status=job_model.status,
            error_message=job_model.error_message,
        )
        return conversion_job

    async def update_conversion_job(self, job_data: ConversionJob) -> None:
        """Updates an existing conversion job record."""
        stmt = select(ConversionJobModel).where(ConversionJobModel.job_id == job_data.job_id)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.status = job_data.status
        row.output_file = job_data.output_file
        row.error_message = job_data.error_message
        row.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def list_user_history(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionHistoryItem], int]:
        """Returns paginated user history jobs with completed and failed records."""
        user_id_int = int(user_id)
        base_stmt = select(ConversionJobModel).where(
            ConversionJobModel.user_id == user_id_int,
            ConversionJobModel.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
        )
        total_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_result = await self.session.execute(total_stmt)
        total = int(total_result.scalar_one())

        rows_result = await self.session.execute(
            base_stmt.order_by(ConversionJobModel.updated_at.desc()).offset(offset).limit(limit)
        )
        rows = rows_result.scalars().all()

        return (
            [
                ConversionHistoryItem(
                    job_id=row.job_id,
                    status=str(row.status),
                    source_format=row.source_format,
                    target_format=row.target_format,
                    input_file=row.input_file,
                    output_file=row.output_file,
                    error_message=row.error_message,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ],
            total,
        )

    async def list_user_active_jobs(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ActiveQueueItem], int]:
        """Returns paginated user jobs that are still queued or processing."""
        user_id_int = int(user_id)
        active_statuses = [JobStatus.AWAITING_UPLOAD, JobStatus.PENDING, JobStatus.PROCESSING]
        base_stmt = select(ConversionJobModel).where(
            ConversionJobModel.user_id == user_id_int,
            ConversionJobModel.status.in_(active_statuses),
        )
        total_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_result = await self.session.execute(total_stmt)
        total = int(total_result.scalar_one())

        rows_result = await self.session.execute(
            base_stmt.order_by(ConversionJobModel.created_at.desc()).offset(offset).limit(limit)
        )
        rows = rows_result.scalars().all()

        return (
            [
                ActiveQueueItem(
                    job_id=row.job_id,
                    status=str(row.status),
                    source_format=row.source_format,
                    target_format=row.target_format,
                    input_file=row.input_file,
                    created_at=row.created_at,
                )
                for row in rows
            ],
            total,
        )