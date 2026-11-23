"""Contrato HTTP e limites da aplicação nesta entrega."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(authorization) -> Iterator[TestClient]:
    with TestClient(
        create_app(authorization=authorization),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {authorization.token}"},
    ) as test_client:
        yield test_client


def test_health_reports_http_and_persistence_readiness(client: TestClient) -> None:
    for _ in range(2):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_health_rejects_mutating_methods(client: TestClient, method: str) -> None:
    assert client.request(method, "/health").status_code == 405


@pytest.mark.parametrize("path", ["/", "/docs", "/redoc", "/openapi.json", "/health/extra"])
def test_unimplemented_routes_are_not_available(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("host", ["example.com", "localhost", "192.168.1.10"])
def test_untrusted_host_is_rejected(client: TestClient, host: str) -> None:
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_browser_origin_is_not_authorized_by_cors(client: TestClient) -> None:
    headers = {"Origin": "https://example.com"}
    response = client.get("/health", headers=headers)
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers

    preflight = client.options(
        "/health",
        headers={**headers, "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 403
    assert "access-control-allow-origin" not in preflight.headers
