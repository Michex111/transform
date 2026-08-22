import pytest

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus


def test_valid_job_creation_defaults_to_pending_status() -> None:
    job = ConversionJob(
        job_id="job-1",
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file="s3-file_store/invoice.pdf",
    )

    assert job.status == JobStatus.AWAITING_UPLOAD
    assert job.output_file is None
    assert job.error_message is None


def test_start_processing_transitions_pending_to_processing(conversion_job: ConversionJob) -> None:
    conversion_job.pending_processing()
    conversion_job.start_processing()

    assert conversion_job.status == JobStatus.PROCESSING


@pytest.mark.parametrize("status", [JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED])
def test_start_processing_raises_for_invalid_status(
    conversion_job: ConversionJob,
    status: JobStatus,
) -> None:
    conversion_job.status = status

    with pytest.raises(InvalidStateTransition):
        conversion_job.start_processing()


def test_complete_sets_output_and_completed_status(conversion_job: ConversionJob) -> None:
    conversion_job.pending_processing()
    conversion_job.start_processing()

    conversion_job.complete("s3-file_store/output.md")

    assert conversion_job.status == JobStatus.COMPLETED
    assert conversion_job.output_file == "s3-file_store/output.md"


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.FAILED])
def test_complete_raises_when_status_is_not_processing(
    conversion_job: ConversionJob,
    status: JobStatus,
) -> None:
    conversion_job.status = status

    with pytest.raises(InvalidStateTransition):
        conversion_job.complete("s3-file_store/output.md")


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.PROCESSING])
def test_fail_sets_failed_status_and_message(
    conversion_job: ConversionJob,
    status: JobStatus,
) -> None:
    conversion_job.status = status

    conversion_job.fail("boom")

    assert conversion_job.status == JobStatus.FAILED
    assert conversion_job.error_message == "boom"


def test_fail_raises_if_job_already_completed(conversion_job: ConversionJob) -> None:
    conversion_job.status = JobStatus.COMPLETED

    with pytest.raises(InvalidStateTransition):
        conversion_job.fail("late failure")


def test_retry_resets_failed_job_to_pending_keeping_object_key(conversion_job: ConversionJob) -> None:
    conversion_job.object_key = "uploads/input.pdf"
    conversion_job.pending_processing()
    conversion_job.start_processing()
    conversion_job.fail("transient error")
    conversion_job.set_compute_result(duration_ms=500, credits=3)

    conversion_job.retry()

    assert conversion_job.status == JobStatus.PENDING
    assert conversion_job.error_message is None
    assert conversion_job.output_file is None
    assert conversion_job.compute_duration_ms == 0
    assert conversion_job.credits_used == 0
    # The input object key is preserved so the worker reuses the existing file.
    assert conversion_job.object_key == "uploads/input.pdf"


@pytest.mark.parametrize(
    "status",
    [JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.AWAITING_UPLOAD],
)
def test_retry_raises_when_job_is_not_failed(
    conversion_job: ConversionJob,
    status: JobStatus,
) -> None:
    conversion_job.status = status
    conversion_job.object_key = "uploads/input.pdf"

    with pytest.raises(InvalidStateTransition):
        conversion_job.retry()


def test_retry_raises_without_object_key(conversion_job: ConversionJob) -> None:
    conversion_job.status = JobStatus.FAILED
    conversion_job.object_key = ""

    with pytest.raises(InvalidStateTransition, match="object_key"):
        conversion_job.retry()