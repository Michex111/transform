from dataclasses import dataclass, asdict
from src.domain.conversions.entities.conversion_job import ConversionJob

@dataclass
class ConversionJobMessage:
    job_id: str
    source_format: str
    target_format: str
    input_key: str
    user_id: str | None = None
    retries: int = 0

    @classmethod
    def from_conversion_job(cls, job: ConversionJob):
        return cls(
            job_id=job.job_id,
            source_format=job.conversion.source_format,
            target_format=job.conversion.target_format,
            input_key=job.input_file,
            user_id=str(job.user_id) if job.user_id is not None else None,
        )

    def to_dict(self) -> dict:
        # Redis streams require all field values to be strings; drop None fields.
        return {k: v for k, v in asdict(self).items() if v is not None}
    

