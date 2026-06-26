from src.domain.value_object.job_status import JobStatus


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

    assert set(payload.keys()) == {"job_id", "progress", "status", "message"}
    assert payload["job_id"] == "job-1"