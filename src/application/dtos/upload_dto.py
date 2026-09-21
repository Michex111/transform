from pydantic import BaseModel, Field

class UploadSession(BaseModel):
    upload_id: str
    object_key: str
    status: str = "pending"
    file_name: str | None = None
    file_extension: str | None = None
    folder_id: str | None = None
    user_id: str | None = None

class UploadResponse(BaseModel):
    upload_id: str = Field(..., description="Unique identifier for the upload session")
    object_key: str = Field(..., description="Presigned URL for the PUT request")
    upload_url: str = Field(..., description="The secure destination path of the object")
    expires_in_minutes: int = Field(..., description="Time in minutes until the upload URL expires.")
