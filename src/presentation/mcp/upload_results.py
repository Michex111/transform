"""Typed MCP results for the attachment-upload tools.

WHY models rather than bare dicts: ``structured_output=True`` publishes a JSON
Schema in ``tools/list``, and a typed return model is what makes that schema
describe the real result — including the fact that a refusal is still a result
(``ok: false`` plus a sentence) rather than a missing field. An agent that reads
the schema can therefore tell what it will get before it calls.

The refusal fields mirror the document tools: ``error`` is always present and
human-actionable, ``required_scope`` names a missing permission, and
``reconnect_required`` says the grant itself must be re-approved. Typing them
here is what keeps ``create_upload_link`` from inventing a second vocabulary.
"""

from pydantic import BaseModel, ConfigDict


class UploadFilePayload(BaseModel):
    """The Drive file an upload produced. Never a storage key."""

    file_id: str
    file_name: str
    extension: str
    size_bytes: int | None = None
    folder_id: str | None = None
    created_at: str | None = None


class UploadJobPayload(BaseModel):
    """The conversion started after an upload, if the link requested one."""

    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str | None = None
    output_file: str | None = None
    progress: int | None = None
    created_at: str | None = None
    error_message: str | None = None
    download_available: bool = False


class UploadLinkResult(BaseModel):
    """What ``create_upload_link`` returns: the capability, or a refusal."""

    # A refusal from the toolbox carries a few extra explanation keys
    # (``hint``, ``source_format``, ``target_formats``). They are preserved
    # rather than stripped, because they are exactly what makes the refusal
    # actionable, but they are not required for success.
    model_config = ConfigDict(extra="allow")

    ok: bool
    error: str | None = None
    required_scope: str | None = None
    reconnect_required: bool | None = None

    upload_id: str | None = None
    expires_in_seconds: int | None = None
    max_bytes: int | None = None
    upload_url: str | None = None
    upload_header_name: str | None = None
    upload_capability: str | None = None
    curl_command: str | None = None
    upload_page_url: str | None = None
    note: str | None = None


class UploadStatusResult(BaseModel):
    """What ``get_upload_status`` returns, once an upload has been attempted."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    error: str | None = None
    required_scope: str | None = None
    reconnect_required: bool | None = None

    upload_id: str | None = None
    status: str | None = None
    file_name: str | None = None
    extension: str | None = None
    expires_at: str | None = None
    file: UploadFilePayload | None = None
    job: UploadJobPayload | None = None
    download_url: str | None = None
    expires_in_seconds: int | None = None


class ImportFileResult(BaseModel):
    """What ``import_file_from_url`` returns: the created file and job, or a refusal."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    error: str | None = None
    required_scope: str | None = None
    reconnect_required: bool | None = None

    url: str | None = None
    file: UploadFilePayload | None = None
    job: UploadJobPayload | None = None
