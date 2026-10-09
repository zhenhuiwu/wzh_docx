"""Retired approval and block-candidate APIs stay absent from the current app."""

from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/projects/{id}/reviews"),
    ("POST", "/api/v1/reviews"),
    ("POST", "/api/v1/reviews/{id}/decision"),
    ("GET", "/api/v1/documents/{id}/blocks"),
    ("POST", "/api/v1/blocks/{id}/revisions"),
    ("POST", "/api/v1/blocks/{id}/revisions/{other}/adopt"),
    ("POST", "/api/v1/blocks/{id}/revisions/{other}/discard"),
])
def test_retired_review_and_block_candidate_routes_are_not_exposed(method, path):
    with TestClient(create_app()) as client:
        response = client.request(method, path.format(id=uuid4(), other=uuid4()),
                                  **({"json": {}} if method == "POST" else {}))
        assert response.status_code == 404, response.text
