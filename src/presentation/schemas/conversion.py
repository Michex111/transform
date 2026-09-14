from pydantic import BaseModel, Field


class CreateConversionJobRequest(BaseModel):
    # ``source_format`` is required only for the upload flow. When ``file_id``
    # is provided, it is inferred from the library file's name instead.
    source_format: str | None = None
    target_format: str = Field(min_length=1, max_length=20)
    input_key: str | None = None
    # When set, the job converts a file already stored in the user's library
    # (object storage) without re-uploading it.
    file_id: str | None = None
    # Client-side (FENCR) encryption. The client encrypts the file in the
    # browser with a fresh per-file data key and uploads the FENCR blob. The
    # client may send the RAW data key (base64) here; the server wraps it
    # immediately with the per-user Fernet key and never persists it in the
    # clear. ``client_encrypted`` flags that the uploaded object is FENCR.
    data_key: str | None = None            # raw 32-byte key, base64 (not stored in clear)
    client_encrypted: bool = False


class ConversionJobResponse(BaseModel):
    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    output_file: str | None = None
    object_key: str | None = None
    download_url: str | None = None
    error_message: str | None = None
    credits_used: int = 0
    compute_duration_ms: int = 0
    # Client-side encryption metadata echoed back so the client can confirm its
    # FENCR blob was registered (and see the wrapped key is stored, not raw).
    data_key_wrapped: str | None = None
    client_encrypted: bool = False


class ConversionHistoryResponse(BaseModel):
    """Paginated list of a user's conversion jobs."""

    jobs: list[ConversionJobResponse]
    total: int
    page: int
    page_size: int


class SupportedConversionResponse(BaseModel):
    source_format: str
    target_format: str


class ConversionMapResponse(BaseModel):
    """Map of every supported source format to its valid target formats."""

    conversions: dict[str, list[str]]
