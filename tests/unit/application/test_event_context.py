from src.domain.conversions.value_object.job_status import JobStatus


def test_event_context_downloading_sets_processing_state(event_context) -> None:
    event_context.downloading()

    assert event_context.status == JobStatus.PROCESSING
    assert event_context.progress == 25
    assert event_context.message == "downloading file"


def test_event_context_chained_transitions_end_in_completed(event_context) -> None:
    payload = event_context.downloading().processing().uploading().completed().to_dict()

    assert payload["status"] == JobStatus.COMPLETED
    assert payload["progress"] == 100
    assert payload["message"] == "conversion completed"


def test_event_context_to_dict_contains_expected_shape(event_context) -> None:
    payload = event_context.processing().to_dict()

    # Pinned exactly: this dict is spread straight into the published event, so
    # a field added to the dataclass silently becomes part of the wire format.
    assert set(payload.keys()) == {
        "job_id",
        "progress",
        "status",
        "message",
        "compute_duration_ms",
        "credits_used",
        "input_size_bytes",
        "output_size_bytes",
        "output_file",
    }
    assert payload["job_id"] == "job-1"


def test_completed_carries_the_measured_file_sizes(event_context) -> None:
    """The terminal event reports the bytes moved, which is where the expanded
    job panel gets them without waiting for a history refresh."""
    payload = event_context.completed(
        compute_duration_ms=1200,
        credits_used=7,
        input_size_bytes=4_300_000,
        output_size_bytes=1_100_000,
    ).to_dict()

    assert payload["input_size_bytes"] == 4_300_000
    assert payload["output_size_bytes"] == 1_100_000


def test_completed_carries_the_output_object_key(event_context) -> None:
    """The terminal event names the object that was actually stored. Without
    it a client can only name the download after the target format, so a
    container conversion (e.g. multi-page pdf -> jpg emits a .zip) is saved as
    a .jpg and then rejected by the upload magic-byte check."""
    payload = event_context.completed(
        output_file="output/user/1/job/job-1/multi.zip"
    ).to_dict()

    assert payload["output_file"] == "output/user/1/job/job-1/multi.zip"


def test_non_terminal_events_report_no_sizes(event_context) -> None:
    """Sizes default to 0 (\"not measured\") on the progress-only events, so the
    client never reads a size off a job that has not been converted yet. The
    output key is likewise absent until there is a stored output to name."""
    payload = event_context.uploading().to_dict()

    assert payload["input_size_bytes"] == 0
    assert payload["output_size_bytes"] == 0
    assert payload["output_file"] is None