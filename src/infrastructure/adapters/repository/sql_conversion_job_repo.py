from datetime import UTC, datetime

from src.infrastructure.database.models import ConversionJobModel
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

class SQLConversionJobRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """
        Save a conversion job to the database (insert or update by job_id).

        Args:
            job_data: The ConversionJob entity to be saved.
        """
        job_model = ConversionJobModel(
            job_id=job_data.job_id,
            status=job_data.status,
            source_format=job_data.conversion.source_format,
            target_format=job_data.conversion.target_format,
            input_file=job_data.input_file,
            output_file=job_data.output_file,
            object_key=job_data.object_key,
            user_id=job_data.user_id,
            error_message=job_data.error_message,
            compute_duration_ms=job_data.compute_duration_ms,
            credits_used=job_data.credits_used,
        )
        self.session.add(job_model)
        await self.session.commit()

    async def update_conversion_job(self, job: ConversionJob) -> None:
        """
        Persist progress updates for an existing job (status, output, errors,
        compute time and credits consumed).
        """
        stmt = (
            update(ConversionJobModel)
            .where(ConversionJobModel.job_id == job.job_id)
            .values(
                status=job.status,
                object_key=job.object_key,
                output_file=job.output_file,
                error_message=job.error_message,
                compute_duration_ms=job.compute_duration_ms,
                credits_used=job.credits_used,
                updated_at=datetime.now(UTC),
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """
        Retrieve a conversion job from the database by its ID.

        Args:
            job_id: The ID of the conversion job to retrieve.
            
        Returns:
            A ConversionJob entity corresponding to the given job_id, or None if not found.
        """

        stmt = select(ConversionJobModel).where(ConversionJobModel.job_id == job_id)
        result = await self.session.execute(stmt)
        job_model = result.scalar_one_or_none()

        if job_model is None:
            return None

        return self._to_entity(job_model)

    async def list_user_history(
        self,
        user_id: int,
        offset: int,
        limit: int,
        since: datetime | None = None,
    ) -> tuple[list[ConversionJob], int]:
        """Returns the user's job history (newest first) plus the total count.

        When ``since`` is provided only jobs created on/after that timestamp are
        returned.
        """
        base = select(ConversionJobModel).where(ConversionJobModel.user_id == user_id)
        if since is not None:
            base = base.where(ConversionJobModel.created_at >= since)

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_q)).scalar_one()

        rows_q = (
            base.order_by(ConversionJobModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(rows_q)).scalars().all()
        return [self._to_entity(r) for r in rows], total

    async def delete_job(self, job_id: str, user_id: int) -> bool:
        """Delete a single job owned by ``user_id``. Returns True when a row was
        removed. Jobs owned by another user (or missing) are untouched."""
        result = await self.session.execute(
            delete(ConversionJobModel).where(
                ConversionJobModel.job_id == job_id,
                ConversionJobModel.user_id == user_id,
            )
        )
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def list_user_active_jobs(
        self,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionJob], int]:
        """Returns user jobs in pending/processing states plus the total count."""
        from src.domain.conversions.value_object.job_status import JobStatus

        base = (
            select(ConversionJobModel)
            .where(ConversionJobModel.user_id == user_id)
            .where(
                ConversionJobModel.status.in_(
                    [JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.AWAITING_UPLOAD]
                )
            )
        )

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_q)).scalar_one()

        rows_q = (
            base.order_by(ConversionJobModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.session.execute(rows_q)).scalars().all()
        return [self._to_entity(r) for r in rows], total

    async def count_by_status(self, user_id: int) -> dict[str, int]:
        """Count jobs grouped by status for a user."""
        stmt = (
            select(ConversionJobModel.status, func.count())
            .where(ConversionJobModel.user_id == user_id)
            .group_by(ConversionJobModel.status)
        )
        result = await self.session.execute(stmt)
        counts: dict[str, int] = {"COMPLETED": 0, "FAILED": 0, "TOTAL": 0}
        for status, count in result.all():
            counts["TOTAL"] += count
            if status in counts:
                counts[status] = count
        return counts

    async def sum_credits_used(self, user_id: int) -> int:
        """Total credits consumed across all of a user's jobs."""
        result = await self.session.execute(
            select(func.coalesce(func.sum(ConversionJobModel.credits_used), 0)).where(
                ConversionJobModel.user_id == user_id
            )
        )
        return result.scalar_one()

    def _to_entity(self, job_model: ConversionJobModel) -> ConversionJob:
        return ConversionJob(
            job_id=job_model.job_id,
            conversion=ConversionType(job_model.source_format, job_model.target_format),
            input_file=job_model.input_file,
            output_file=job_model.output_file,
            object_key=job_model.object_key,
            status=job_model.status,
            error_message=job_model.error_message,
            compute_duration_ms=job_model.compute_duration_ms,
            credits_used=job_model.credits_used,
            user_id=job_model.user_id,
        )