import pytest

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_origin import JobOrigin
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


def test_origin_defaults_to_web() -> None:
    """A caller that knows nothing about origin must be unchanged."""
    job = ConversionJob(
        job_id="job-1",
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file="s3-file_store/invoice.pdf",
    )

    assert job.origin is JobOrigin.WEB


def test_origin_is_appended_last_so_positional_construction_still_maps() -> None:
    """``origin`` was added **last** to keep positional callers working.

    A previous change in this repo inserted a field in the middle of this
    dataclass and silently remapped every positional argument after it. This
    pins the order: ten positional arguments must still land on the same ten
    fields, and ``origin`` must fall to its default.
    """
    job = ConversionJob(
        "job-1",
        ConversionType("pdf", "docx"),
        "s3-file_store/invoice.pdf",
        "s3-file_store/out.docx",
        "uploads/invoice.pdf",
        JobStatus.PENDING,
        None,
        1200,
        3,
        99,
    )

    assert job.job_id == "job-1"
    assert job.input_file == "s3-file_store/invoice.pdf"
    assert job.output_file == "s3-file_store/out.docx"
    assert job.object_key == "uploads/invoice.pdf"
    assert job.status is JobStatus.PENDING
    assert job.error_message is None
    assert job.compute_duration_ms == 1200
    assert job.credits_used == 3
    assert job.user_id == 99
    assert job.origin is JobOrigin.WEB


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
    conversion_job.set_compute_result(
        duration_ms=500, credits=3, input_size_bytes=2048, output_size_bytes=900
    )

    conversion_job.retry()

    assert conversion_job.status == JobStatus.PENDING
    assert conversion_job.error_message is None
    assert conversion_job.output_file is None
    assert conversion_job.compute_duration_ms == 0
    assert conversion_job.credits_used == 0
    # The previous attempt's sizes describe a file this run replaces, so they are
    # cleared with the rest of the result rather than left to look current.
    assert conversion_job.input_size_bytes == 0
    assert conversion_job.output_size_bytes == 0
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