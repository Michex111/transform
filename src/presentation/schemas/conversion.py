from pydantic import BaseModel, Field


class CreateConversionJobRequest(BaseModel):
    source_format: str = Field(min_length=1, max_length=20)
    target_format: str = Field(min_length=1, max_length=20)
    input_key: str = Field(min_length=1)
    expected_file_size_bytes: int = Field(default=0, ge=0)


class ConversionJobResponse(BaseModel):
    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    output_file: str | None = None
    download_url: str | None = None
    queue_stream: str | None = None
    credits_remaining: int | None = None


class SupportedConversionResponse(BaseModel):
    source_format: str
    target_format: str
