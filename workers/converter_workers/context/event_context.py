from dataclasses import dataclass, asdict

from src.domain.conversions.value_object.job_status import JobStatus


@dataclass
class EventContext:
    job_id: str
    progress: int = 0
    status: str = "PENDING"
    message: str | None = None
    compute_duration_ms: int = 0
    credits_used: int = 0
    # Plaintext bytes moved, measured on disk. 0 means "not measured"; only the
    # terminal event carries a real value.
    input_size_bytes: int = 0
    output_size_bytes: int = 0

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

    def completed(
        self,
        compute_duration_ms: int = 0,
        credits_used: int = 0,
        input_size_bytes: int = 0,
        output_size_bytes: int = 0,
    ):
        self.status = JobStatus.COMPLETED
        self.progress = 100
        self.message = "conversion completed"
        self.compute_duration_ms = compute_duration_ms
        self.credits_used = credits_used
        self.input_size_bytes = input_size_bytes
        self.output_size_bytes = output_size_bytes
        return self

    def failed(self, error_message: str):
        self.status = JobStatus.FAILED
        self.message = error_message
        return self

    def to_dict(self) -> dict:
        return asdict(self)
        