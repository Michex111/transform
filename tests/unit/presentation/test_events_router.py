"""Tests for the SSE events router forwarding credit fields to the client."""

import json

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_event_subscriber,
)
from tests.integration.dependencies.api_overrides import create_test_client


class FakeJobRepository:
    def __init__(self, job: ConversionJob) -> None:
        self.job = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        del job_id
        return self.job


class FakeSubscriber:
    """Yield a single terminal event carrying credit fields."""

    def __init__(self, fields: dict) -> None:
        self.fields = fields

    async def iter_events(self, job_id: str):
        del job_id
        yield ("1", self.fields)


def _completed_job() -> ConversionJob:
    return ConversionJob(
        job_id="job-1",
        conversion=ConversionType("pdf", "docx"),
        input_file="in.pdf",
        object_key="k",
        user_id=101,
        status=JobStatus.COMPLETED,
    )


def test_events_router_forwards_credits_remaining() -> None:
    fields = {
        "job_id": "job-1",
        "status": "COMPLETED",
        "progress": "100",
        "message": "conversion completed",
        "compute_duration_ms": "1200",
        "credits_used": "4",
        "credits_remaining": "46",
    }
    with create_test_client() as client:
        client.app.dependency_overrides[get_conversion_repository] = lambda: FakeJobRepository(
            _completed_job()
        )
        client.app.dependency_overrides[get_event_subscriber] = lambda: FakeSubscriber(fields)
        with client.stream("GET", "/api/v1/events/jobs/job-1") as stream:
            lines = [line for line in stream.iter_lines() if line]

    data_lines = [l for l in lines if l.startswith("data: ")]
    payloads = [json.loads(l[len("data: "):]) for l in data_lines]
    completed = [p for p in payloads if p.get("status") == "COMPLETED"]
    assert completed, "expected a COMPLETED event payload"
    assert completed[0]["credits_used"] == "4"
    assert completed[0]["credits_remaining"] == "46"


def test_events_router_omits_credit_fields_when_absent() -> None:
    # A terminal COMPLETED event without credit fields: the SSE generator must
    # stop and must not add credit keys to the payload.
    fields = {
        "job_id": "job-1",
        "status": "COMPLETED",
        "progress": "100",
        "message": "conversion completed",
    }
    with create_test_client() as client:
        client.app.dependency_overrides[get_conversion_repository] = lambda: FakeJobRepository(
            _completed_job()
        )
        client.app.dependency_overrides[get_event_subscriber] = lambda: FakeSubscriber(fields)
        with client.stream("GET", "/api/v1/events/jobs/job-1") as stream:
            lines = [line for line in stream.iter_lines() if line]

    data_lines = [l for l in lines if l.startswith("data: ")]
    payloads = [json.loads(l[len("data: "):]) for l in data_lines]
    completed = [p for p in payloads if p.get("status") == "COMPLETED"]
    assert completed
    assert "credits_remaining" not in completed[0]
    assert "credits_used" not in completed[0]
