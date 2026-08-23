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
