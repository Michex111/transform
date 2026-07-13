from src.infrastructure.database.session import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Enum as SqlEnum, String, Integer

from src.domain.value_object.job_status import JobStatus

class ConversionJobModel(Base):
    __tablename__ = "conversion_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(SqlEnum(JobStatus), nullable=False)
    source_format: Mapped[str] = mapped_column(String, nullable=False)
    target_format: Mapped[str] = mapped_column(String, nullable=False)
    input_file: Mapped[str] = mapped_column(String, nullable=False)
    output_file: Mapped[str | None] = mapped_column(String, nullable=True)
    
