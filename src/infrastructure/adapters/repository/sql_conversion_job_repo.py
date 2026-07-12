from infrastructure.database.models import ConversionJobModel
from domain.entities.conversion_job import ConversionJob
from domain.value_object.conversion_type import ConversionType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

class SQLConversionJobRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """
        Save a conversion job to the database.

        Args:
            job_data: The ConversionJob entity to be saved.
        """
        job_model = ConversionJobModel(
            job_id=job_data.job_id,
            status=job_data.status,
            source_format=job_data.conversion.source_format,
            target_format=job_data.conversion.target_format,
            input_file=job_data.input_file,
            output_file=job_data.output_file
        )
        self.session.add(job_model)
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

        conversion_job = ConversionJob(
            job_id=job_model.job_id,
            conversion=ConversionType(job_model.source_format, job_model.target_format),
            input_file=job_model.input_file,
            output_file=job_model.output_file,
            status=job_model.status
        )
        return conversion_job