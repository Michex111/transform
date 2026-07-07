from src.infrastructure.database.session import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Enum as SqlEnum, String, Integer

class ConversionJobModel(Base):
    __tablename__ = "conversion_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    
