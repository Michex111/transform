import pytest

from workers.converter_workers.context.event_context import EventContext


@pytest.fixture
def event_context() -> EventContext:
    return EventContext(job_id="job-1")