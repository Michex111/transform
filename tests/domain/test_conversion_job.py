import pytest

from src.domain.entities.conversion_job import ConversionJob
from src.domain.exceptions import InvalidStateTransition
from src.domain.value_object.conversion_type import ConversionType
from src.domain.value_object.job_status import JobStatus


def test_valid_job_creation_defaults_to_pending_status() -> None:
    job = ConversionJob(
        job_id="job-1",
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file="s3-file_store/invoice.pdf",
    )

    assert job.status == JobStatus.PENDING
    assert job.output_file is None
    assert job.error_message is None


def test_start_processing_transitions_pending_to_processing(conversion_job: ConversionJob) -> None:
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