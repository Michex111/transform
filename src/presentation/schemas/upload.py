from pydantic import BaseModel, Field


class CreateUploadSessionRequest(BaseModel):
    file_extension: str = Field(min_length=1, max_length=20)
    file_name: str | None = Field(default=None, max_length=255, description="Original file name")
    folder_id: str | None = Field(default=None, description="Destination folder, or None for root")
