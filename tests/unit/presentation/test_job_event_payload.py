"""Tests for the typed SSE job-event payload builder.

Regression guard: the Redis stream stores every field as a string, so the
payload used to carry ``"progress": "25"`` (and string credits/sizes). The SPA
reads ``progress`` as a number, so a string made it render an indeterminate
sweep instead of the real percentage.
"""

from src.presentation.api.routers.v1.job_event_payload import job_event_payload


def test_stringified_progress_becomes_a_number() -> None:
    payload = job_event_payload(
        "job-1",
        {"job_id": "job-1", "status": "PROCESSING", "progress": "25", "message": "downloading file"},
    )
    assert payload == {
        "job_id": "job-1",
        "status": "PROCESSING",
        "progress": 25,
        "message": "downloading file",
    }


def test_optional_integer_fields_are_coerced() -> None:
    payload = job_event_payload(
        "job-1",
        {
            "status": "COMPLETED",
            "progress": "100",
            "compute_duration_ms": "1200",
            "credits_used": "4",
            "input_size_bytes": "10",
            "output_size_bytes": "5",
            "credits_remaining": "46",
            "output_file": "outputs/1/job-1/report.docx",
        },
    )
    assert payload["compute_duration_ms"] == 1200
    assert payload["credits_used"] == 4
    assert payload["input_size_bytes"] == 10
    assert payload["output_size_bytes"] == 5
    assert payload["credits_remaining"] == 46
    assert payload["output_file"] == "outputs/1/job-1/report.docx"


def test_absent_progress_defaults_to_zero_not_a_string() -> None:
    payload = job_event_payload("job-1", {"status": "PENDING"})
    assert payload["progress"] == 0
    assert isinstance(payload["progress"], int)


def test_unparseable_and_absent_optional_fields_are_omitted() -> None:
    # A malformed value must not become a fabricated 0 — the SPA treats 0 bytes
    # as "never measured" and omits the size line entirely.
    payload = job_event_payload("job-1", {"status": "PROCESSING", "input_size_bytes": ""})
    assert "input_size_bytes" not in payload
    assert "credits_used" not in payload


def test_empty_output_file_is_omitted() -> None:
    payload = job_event_payload("job-1", {"status": "PROCESSING", "progress": "50", "output_file": ""})
    assert "output_file" not in payload
