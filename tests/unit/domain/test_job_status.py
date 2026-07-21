from src.domain.conversions.value_object.job_status import JobStatus


def test_job_status_defines_expected_members() -> None:
    assert {status.name for status in JobStatus} == {
        "AWAITING_UPLOAD",
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    }


def test_job_status_string_values_are_upper_case() -> None:
    assert JobStatus.AWAITING_UPLOAD.value == "AWAITING_UPLOAD"
    assert JobStatus.PENDING.value == "PENDING"
    assert JobStatus.PROCESSING.value == "PROCESSING"
    assert JobStatus.COMPLETED.value == "COMPLETED"
    assert JobStatus.FAILED.value == "FAILED"