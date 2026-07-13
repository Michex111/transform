from src.domain.value_object.job_status import JobStatus


def test_job_status_defines_expected_members() -> None:
    assert {status.name for status in JobStatus} == {
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
    }


def test_job_status_string_values_are_lower_case() -> None:
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.PROCESSING.value == "processing"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"