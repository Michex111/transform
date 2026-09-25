from pydantic import BaseModel, Field


class CreateUploadSessionRequest(BaseModel):
    file_extension: str = Field(min_length=1, max_length=20)
    file_name: str | None = Field(default=None, max_length=255, description="Original file name")
    folder_id: str | None = Field(default=None, description="Destination folder, or None for root")
    # The size the client intends to upload, in bytes. Optional so the guest
    # flow and older clients keep working unchanged (omitted => the historical
    # single-PUT session with no pre-flight check). When supplied it drives two
    # server-side decisions: whether the session uses multipart upload, and
    # whether the upload is refused BEFORE it starts (per-file cap / storage
    # quota). Bounded below at 1 byte, which turns a zero/negative/absurd value
    # into a 422 rather than silently meaning "no size declared".
    file_size: int | None = Field(
        default=None,
        ge=1,
        description="Declared size of the file in bytes (optional)",
    )


class UploadPartEtag(BaseModel):
    part_number: int = Field(ge=1)
    etag: str = Field(min_length=1, description="The ETag response header of the part PUT")


class UploadVerifyRequest(BaseModel):
    """Optional body for ``POST /api/uploads/sessions/{id}/verify``.

    Required for a multipart session (the provider cannot assemble the object
    without the part etags) and omitted for a single-PUT session, which is why
    the whole body is optional on the endpoint.
    """

    parts: list[UploadPartEtag] | None = Field(
        default=None,
        description="Part number/ETag pairs for a multipart upload",
    )


class UploadPartUrlsRequest(BaseModel):
    """Body for ``POST /api/uploads/sessions/{id}/parts``.

    Capped at 100 per call: one request mints one batch of presigned URLs, which
    bounds both the response size and how long a single call can take. A client
    uploading 80 parts therefore needs one call; a client with more parts pages
    through them.
    """

    part_numbers: list[int] = Field(min_length=1, max_length=100)


class UploadPartUrl(BaseModel):
    part_number: int
    url: str


class UploadPartUrlsResponse(BaseModel):
    parts: list[UploadPartUrl]
    expires_in_minutes: int

