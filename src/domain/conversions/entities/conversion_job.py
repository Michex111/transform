from dataclasses import dataclass
from datetime import datetime

from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus

@dataclass
class ConversionJob:
    job_id: str
    conversion: ConversionType
    input_file: str
    output_file: str | None = None
    object_key: str = ""  # S3/MinIO object key for the input file
    status: JobStatus = JobStatus.AWAITING_UPLOAD
    error_message: str | None = None
    compute_duration_ms: int = 0          # actual converter wall-clock time
    credits_used: int = 0                 # credits charged for this job
    user_id: int | None = None            # owner of the job (when authenticated)
    # Client-side (FENCR) encryption: the browser encrypts the file BEFORE
    # upload with a per-file data key. The server only ever receives the key in
    # wrapped (Fernet-encrypted) form and NEVER persists the raw key.
    data_key_wrapped: str | None = None   # Fernet ciphertext (b64 str) of the raw data key
    client_encrypted: bool = False        # True when the input is a FENCR blob
    # When the row was inserted, straight from the database. Read-only as far as
    # the domain is concerned (nothing transitions it), and `None` for a job
    # that has not been persisted yet. Without it the API has no timestamp to
    # return, which is why every row listed from history showed a blank date.
    created_at: datetime | None = None
    # Sizes of the plaintext files this job moved, in bytes, as measured on disk
    # by the worker. `input_size_bytes` is the *decrypted* input when encryption
    # is in play, i.e. the size of the file the user actually handed us -- not
    # the size of the stored ciphertext, which is a few bytes larger and
    # meaningless to them. Both stay 0 until the worker has run.
    input_size_bytes: int = 0
    output_size_bytes: int = 0

    def pending_processing(self):
        if self.status != JobStatus.AWAITING_UPLOAD:
            raise InvalidStateTransition(f"Cannot transition to pending processing from status {self.status}")
        self.status = JobStatus.PENDING

    def start_processing(self):
        if self.status != JobStatus.PENDING:
            raise InvalidStateTransition(f"Cannot start processing from status {self.status}")
        if not self.object_key:
            raise InvalidStateTransition("Cannot start processing without an object_key set")
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

    def retry(self):
        """Re-enqueue a failed job without re-uploading its input file.

        The input object is already stored at ``object_key``, so we only reset
        the job state back to PENDING and clear the previous failure. The
        worker will download the existing object and try again.
        """
        if self.status != JobStatus.FAILED:
            raise InvalidStateTransition(f"Cannot retry job from status {self.status}")
        if not self.object_key:
            raise InvalidStateTransition("Cannot retry a job without an object_key set")
        self.status = JobStatus.PENDING
        self.error_message = None
        self.output_file = None
        self.compute_duration_ms = 0
        self.credits_used = 0
        # The previous attempt's sizes describe a file this run will replace.
        self.input_size_bytes = 0
        self.output_size_bytes = 0

    def set_compute_result(
        self,
        *,
        duration_ms: int,
        credits: int,
        input_size_bytes: int = 0,
        output_size_bytes: int = 0,
    ) -> None:
        """Record the actual compute time, credits charged and bytes moved.

        The sizes default to 0 so a caller that only knows the timing (an older
        worker, or a test) is unchanged; a value of 0 means "not measured" and
        the API/UI treat it as absent rather than as a real zero-byte file.
        """
        if duration_ms < 0:
            raise ValueError("compute_duration_ms cannot be negative")
        if credits < 0:
            raise ValueError("credits_used cannot be negative")
        if input_size_bytes < 0:
            raise ValueError("input_size_bytes cannot be negative")
        if output_size_bytes < 0:
            raise ValueError("output_size_bytes cannot be negative")
        self.compute_duration_ms = duration_ms
        self.credits_used = credits
        self.input_size_bytes = input_size_bytes
        self.output_size_bytes = output_size_bytes
        
