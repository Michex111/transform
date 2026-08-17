"""Tests for the conversion job queue message serialization."""

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.adapters.queues.messages import ConversionJobMessage


def _job(user_id: int | None = None) -> ConversionJob:
    return ConversionJob(
        job_id="job-1",
        conversion=ConversionType("pdf", "docx"),
        input_file="input.pdf",
        user_id=user_id,
    )


def test_message_includes_user_id_when_present() -> None:
    message = ConversionJobMessage.from_conversion_job(_job(user_id=7))
    assert message.user_id == "7"
    assert message.to_dict()["user_id"] == "7"


def test_message_omits_none_fields_for_redis() -> None:
    """Redis xadd rejects None values, so None fields must be dropped."""
    message = ConversionJobMessage.from_conversion_job(_job(user_id=None))
    payload = message.to_dict()
    assert "user_id" not in payload
    assert payload["job_id"] == "job-1"
    assert payload["source_format"] == "pdf"
    assert payload["target_format"] == "docx"
    assert payload["input_key"] == "input.pdf"


def test_message_roundtrip_preserves_job_fields() -> None:
    job = _job(user_id=3)
    message = ConversionJobMessage.from_conversion_job(job)
    payload = message.to_dict()

    assert payload["job_id"] == "job-1"
    assert payload["source_format"] == "pdf"
    assert payload["target_format"] == "docx"
    assert payload["input_key"] == "input.pdf"
    assert payload["user_id"] == "3"
