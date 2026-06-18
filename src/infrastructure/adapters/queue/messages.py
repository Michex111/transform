from dataclasses import dataclass, asdict
from src.domain.entities.conversion_job import ConversionJob

@dataclass
class ConversionJobMessage:
    job_id: str
    source_format: str
    target_format: str
    input_key: str
    retries: int = 0

    @classmethod
    def from_conversion_job(cls, job: ConversionJob):
        return cls(
            job_id=job.job_id,
            source_format=job.conversion.source_format,
            target_format=job.conversion.target_format,
            input_key=job.input_file
        )
       
    def to_dict(self):
        return asdict(self)