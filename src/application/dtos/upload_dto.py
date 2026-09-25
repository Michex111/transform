from typing import Literal

from pydantic import BaseModel, Field

class UploadSession(BaseModel):
    """The server-side state of one upload, cached (JSON) until it expires.

    Adding a field here changes the cached JSON shape. Old sessions written
    before a new field existed still parse: every added field has a default.
    """

    upload_id: str
    object_key: str
    status: str = "pending"
    file_name: str | None = None
    file_extension: str | None = None
    folder_id: str | None = None
    user_id: str | None = None

    # How the client must send the bytes. ``single`` is the historical one-shot
    # presigned PUT; ``multipart`` means the client PUTs each part to a URL from
    # ``/uploads/sessions/{id}/parts`` and then finalizes with the part list.
    upload_mode: Literal["single", "multipart"] = "single"
    # The size the client DECLARED at session creation, or None when it did not
    # declare one. Recorded for diagnostics only — it is advisory and must never
    # be treated as the authoritative size (the real size is measured with
    # ``stat_object`` at finalize).
    declared_size: int | None = None
    # Provider multipart upload id, present only while a multipart upload is in
    # flight. Cleared once the upload is completed at finalize so a repeated
    # verify cannot try to complete the same upload twice.
    multipart_upload_id: str | None = None
    part_size_bytes: int | None = None
    part_count: int | None = None

class UploadResponse(BaseModel):
    upload_id: str = Field(..., description="Unique identifier for the upload session")
    object_key: str = Field(..., description="Presigned URL for the PUT request")
    upload_url: str | None = Field(
        ...,
        description=(
            "The single-PUT presigned URL; null for a multipart session, where "
            "part URLs are minted from /uploads/sessions/{id}/parts"
        ),
    )
    expires_in_minutes: int = Field(..., description="Time in minutes until the upload URL expires.")
    # --- additive fields (present for every new session) -----------------
    upload_mode: Literal["single", "multipart"] = Field(
        default="single", description="How the client must upload the bytes"
    )
    part_size_bytes: int | None = Field(
        default=None, description="Part size in bytes when upload_mode=multipart"
    )
    part_count: int | None = Field(
        default=None, description="Number of parts when upload_mode=multipart"
    )
    max_file_size_bytes: int | None = Field(
        default=None, description="The caller's effective per-file cap in bytes"
    )

