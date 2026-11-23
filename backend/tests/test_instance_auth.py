"""Nenhum teste concede bypass: aplicações recebem um contexto explícito."""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.core.instance_auth import InstanceAuthorization
from app.main import create_app


@pytest.fixture
def authorization() -> InstanceAuthorization:
    return InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer errado",
        "Basic abc",
        "Bearer " + "0" * 64,
        "Bearer " + "a" * 65,
        "Bearer " + "A" * 64,
    ],
)
def test_rejects_invalid_or_missing_authorization(authorization, header):
    with TestClient(create_app(authorization=authorization), base_url="http://127.0.0.1") as client:
        response = client.get(
            "/health", headers={} if header is None else {"Authorization": header}
        )
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        leaked = authorization.token in response.text
        assert not leaked, "Resposta não pode conter autorização."


def test_context_required_before_startup():
    with pytest.raises(RuntimeError, match="contexto privado"), TestClient(create_app()):
        pass


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://example.com"}, {"Origin": "null"}, {"Sec-Fetch-Site": "same-origin"}],
)
def test_browser_rejected_even_with_valid_authorization(authorization, headers):
    with TestClient(create_app(authorization=authorization), base_url="http://127.0.0.1") as client:
        response = client.get(
            "/health", headers={**headers, "Authorization": f"Bearer {authorization.token}"}
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers


def test_duplicate_authorization_is_rejected(authorization):
    with TestClient(create_app(authorization=authorization), base_url="http://127.0.0.1") as client:
        value = f"Bearer {authorization.token}"
        assert client.get("/health", headers=[("Authorization", value)] * 2).status_code == 401


def test_context_validates_lengths_and_hides_secret():
    with pytest.raises(ValueError):
        InstanceAuthorization(b"x", b"s" * 32)
    assert "73737373" not in repr(InstanceAuthorization(b"i" * 16, b"s" * 32))
