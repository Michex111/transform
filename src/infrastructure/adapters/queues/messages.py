from dataclasses import dataclass, asdict
from src.domain.conversions.entities.conversion_job import ConversionJob


@dataclass
class ConversionJobMessage:
    job_id: str
    source_format: str
    target_format: str
    input_key: str
    object_key: str
    user_id: str | None = None
    retries: int = 0
    # Client-side (FENCR) encryption metadata passed to the worker so it can
    # decrypt the input before conversion. ``client_encrypted`` is serialized
    # as a "true"/"false" string because Redis stream fields must be strings.
    client_encrypted: bool = False
    data_key_wrapped: str | None = None

    @classmethod
    def from_conversion_job(cls, job: ConversionJob):
        if not job.object_key:
            raise ValueError("ConversionJob must have an object_key to create a ConversionJobMessage")
        return cls(
            job_id=job.job_id,
            source_format=job.conversion.source_format,
            target_format=job.conversion.target_format,
            input_key=job.input_file,
            object_key=job.object_key,
            user_id=str(job.user_id) if job.user_id is not None else None,
            client_encrypted=bool(job.client_encrypted),
            data_key_wrapped=job.data_key_wrapped,
        )

    def to_dict(self) -> dict:
        # Redis streams require all field values to be strings; drop None fields.
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        # Boolean values cannot be stored in a Redis stream as native types; the
        # worker parses "true"/"false" back into a bool via ``_to_bool``.
        if "client_encrypted" in payload:
            payload["client_encrypted"] = "true" if payload["client_encrypted"] else "false"
        return payload

    @staticmethod
    def _to_bool(value: str | None) -> bool:
        """Parse a Redis stream boolean field back into a Python bool."""
        return str(value).strip().lower() in {"true", "1", "yes"}

    

