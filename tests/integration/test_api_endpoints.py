from tests.integration.dependencies.api_overrides import create_test_client


def test_health_endpoint_returns_ok() -> None:
    with create_test_client() as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_conversion_job_endpoint_returns_accepted() -> None:
    with create_test_client() as client:
        response = client.post(
            "/api/v1/web/conversions/jobs",
            json={
                "source_format": " DOCX ",
                "target_format": " PDF ",
                "input_key": "uploads/example.docx",
                "expected_file_size_bytes": 1024,
            },
        )
        payload = response.json()
        conversion_service = client.app.state.fake_conversion_service   #type: ignore
        transfer_service = client.app.state.fake_transfer_service       #type: ignore

    assert response.status_code == 202
    assert payload["job_id"] == "test-job-id"
    assert payload["status"] == "AWAITING_UPLOAD"
    assert payload["source_format"] == "docx"
    assert payload["target_format"] == "pdf"
    assert payload["input_file"] == "uploads/example.docx"
    assert payload["download_url"] == "https://storage.test/upload-url"
    assert payload["queue_stream"] == "conversion_jobs:normal"
    assert payload["credits_remaining"] == 99
    assert conversion_service.created_jobs[0].conversion.source_format == "docx"
    assert conversion_service.created_jobs[0].conversion.target_format == "pdf"
    assert transfer_service.calls == [("uploads/example.docx", "101")]


def test_list_conversion_endpoint_returns_supported_conversions() -> None:
    with create_test_client() as client:
        response = client.get("/api/v1/web/conversions/supported")
        payload = response.json()

    assert response.status_code == 200
    assert {"source_format": "docx", "target_format": "pdf"} in payload
    assert {"source_format": "pdf", "target_format": "docx"} in payload