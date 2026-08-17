from dataclasses import dataclass

from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus

@dataclass
class ConversionJob:
    job_id: str
    conversion: ConversionType
    input_file: str
    output_file: str | None = None
    status: JobStatus = JobStatus.AWAITING_UPLOAD
    error_message: str | None = None
    compute_duration_ms: int = 0          # actual converter wall-clock time
    credits_used: int = 0                 # credits charged for this job
    user_id: int | None = None            # owner of the job (when authenticated)

    def pending_processing(self):
        if self.status != JobStatus.AWAITING_UPLOAD:
            raise InvalidStateTransition(f"Cannot transition to pending processing from status {self.status}")
        self.status = JobStatus.PENDING

    def start_processing(self):
        if self.status != JobStatus.PENDING:
            raise InvalidStateTransition(f"Cannot start processing from status {self.status}")
        self.status = JobStatus.PROCESSING

    def complete(self, output_file: str):
        if self.status != JobStatus.PROCESSING:
            raise InvalidStateTransition(f"Cannot complete job from status {self.status}")
        self.output_file = output_file
        self.status = JobStatus.COMPLETED

    def fail(self, error_message: str):
        if self.status not in {JobStatus.PENDING, JobStatus.PROCESSING}:
            raise InvalidStateTransition(f"Cannot fail job from status {self.status}")
        self.error_message = error_message
        self.status = JobStatus.FAILED

    def set_compute_result(self, *, duration_ms: int, credits: int) -> None:
        """Record the actual compute time and credits charged."""
        if duration_ms < 0:
            raise ValueError("compute_duration_ms cannot be negative")
        if credits < 0:
            raise ValueError("credits_used cannot be negative")
        self.compute_duration_ms = duration_ms
        self.credits_used = credits
        
