from pydantic import BaseModel, Field


class CreateUploadSessionRequest(BaseModel):
    file_extension: str = Field(min_length=1, max_length=20)
