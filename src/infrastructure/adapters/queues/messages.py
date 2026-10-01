from dataclasses import dataclass, asdict
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.job_origin import JobOrigin


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
    # How the job's request authenticated, so the worker can apply the
    # origin-aware credit spend order. Serialized as a plain string (Redis
    # stream fields must be strings); an old message that predates the field
    # carries no ``origin`` key at all and the worker degrades it to WEB.
    origin: JobOrigin = JobOrigin.WEB

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
            # Coerced at the entity boundary: the declared type is not a
            # runtime guarantee for a value that came from the database.
            client_encrypted=bool(job.client_encrypted),  # pyrefly: ignore[unnecessary-type-conversion]
            data_key_wrapped=job.data_key_wrapped,
            origin=job.origin,
        )

    def to_dict(self) -> dict:
        # Redis streams require all field values to be strings; drop None fields.
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        # Boolean values cannot be stored in a Redis stream as native types; the
        # worker parses "true"/"false" back into a bool via ``_to_bool``.
        if "client_encrypted" in payload:
            payload["client_encrypted"] = "true" if payload["client_encrypted"] else "false"
        # A StrEnum is a str at runtime, but stringify explicitly so the stream
        # field is unambiguously a plain string regardless of how it arrived.
        if "origin" in payload:
            payload["origin"] = str(payload["origin"])
        return payload

    @staticmethod
    def _to_bool(value: str | None) -> bool:
        """Parse a Redis stream boolean field back into a Python bool."""
        return str(value).strip().lower() in {"true", "1", "yes"}

    

