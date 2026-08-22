from tests.integration.dependencies.api_overrides import create_test_client


def test_health_endpoint_returns_ok() -> None:
    with create_test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_conversion_job_endpoint_returns_accepted() -> None:
    with create_test_client() as client:
        response = client.post(
            "/api/conversions/jobs",
            json={
                "source_format": " DOCX ",
                "target_format": " PDF ",
                "input_key": "uploads/example.docx",
            },
        )
        payload = response.json()
        conversion_service = client.app.state.fake_conversion_service   #type: ignore

    assert response.status_code == 202
    assert payload["job_id"] == "test-job-id"
    assert payload["status"] == "AWAITING_UPLOAD"
    assert payload["source_format"] == "docx"
    assert payload["target_format"] == "pdf"
    assert payload["input_file"] == "uploads/example.docx"
    # object_key is not set until the upload is verified, so it is None here.
    assert payload["object_key"] is None
    assert conversion_service.created_jobs[0].conversion.source_format == "docx"
    assert conversion_service.created_jobs[0].conversion.target_format == "pdf"


def test_list_conversion_endpoint_returns_supported_conversions() -> None:
    with create_test_client() as client:
        response = client.get("/api/conversions/supported")
        payload = response.json()

    assert response.status_code == 200
    assert {"source_format": "docx", "target_format": "pdf"} in payload
    assert {"source_format": "pdf", "target_format": "docx"} in payload


def test_retry_conversion_job_endpoint_returns_retried_job() -> None:
    with create_test_client() as client:
        # Create a job, then mark it FAILED so it is retryable.
        created = client.post(
            "/api/conversions/jobs",
            json={
                "source_format": "docx",
                "target_format": "pdf",
                "input_key": "uploads/example.docx",
            },
        )
        job_id = created.json()["job_id"]
        conversion_service = client.app.state.fake_conversion_service  # type: ignore
        job = conversion_service.created_jobs[0]
        job.object_key = "uploads/example.docx"
        job.pending_processing()
        job.fail("transient error")

        response = client.post(f"/api/conversions/jobs/{job_id}/retry")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job_id
    assert payload["status"] == "PENDING"
    assert payload["object_key"] == "uploads/example.docx"


def test_retry_conversion_job_endpoint_returns_404_for_missing_job() -> None:
    with create_test_client() as client:
        response = client.post("/api/conversions/jobs/nonexistent/retry")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"