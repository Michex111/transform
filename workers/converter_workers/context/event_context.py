from dataclasses import dataclass, asdict
from enum import StrEnum, auto
from typing import TypedDict, Optional

from src.domain.value_object.job_status import JobStatus


@dataclass
class EventContext:
    job_id: str
    progress: int = 0
    status: str = "PENDING"
    message: str | None = None
    
    def downloading(self):
            self.status = JobStatus.PROCESSING
            self.progress = 25
            self.message = "downloading file"

            return self

    def processing(self):
        self.status = JobStatus.PROCESSING
        self.progress = 50
        self.message = "converting file"

        return self
    
    def uploading(self):
        self.status = JobStatus.PROCESSING
        self.progress = 75
        self.message = "uploading file"

        return self
    
    def completed(self):
        self.status = JobStatus.COMPLETED
        self.progress = 100
        self.message = "conversion completed"

        return self
    
    def failed(self, error_message: str):
        self.status = JobStatus.FAILED
        self.message = error_message

        return self

    def to_dict(self) -> dict:
        return asdict(self)
        