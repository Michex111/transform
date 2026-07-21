import pytest

from tests.integration.utils.fast_api_server import FastAPIServer


def test_api_endpoint_pipeline():
    with FastAPIServer("src.presentation.api.main:app", port=37954) as server:
        # Test the health check endpoint

        server.authenticate()
        response = server.session.get(f"{server.base_url}/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    

        # Test creating a conversion job
        payload = {
            "source_format": "docx",
            "target_format": "pdf",
            "input_key": "/path/to/file.docx"
        }
        create_response = server.create_conversion_job(**payload)
        assert create_response["status"] == "AWAITING_UPLOAD"
        assert "job_id" in create_response

def test_list_conversion_endpoint():
    ...