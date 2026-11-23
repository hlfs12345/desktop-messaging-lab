"""Servidor real, descoberta privada e autorização explícita."""

import struct
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
from helpers.private_backend import PrivateBackend

from app.runtime.control_protocol import Code, Kind, write_frame


def test_server_starts_on_loopback_with_system_selected_port(tmp_path: Path):
    with PrivateBackend(tmp_path / "db") as backend:
        url = backend.ready()
        with httpx2.Client(
            base_url=url, headers=backend.headers, timeout=3, trust_env=False
        ) as client:
            for _ in range(2):
                response = client.get("/health?probe=nao_registrar")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                assert response.headers["cache-control"] == "no-store"
                assert "server" not in response.headers
            assert client.post("/health").status_code == 405
            assert client.get("/health", headers={"Host": "example.com"}).status_code == 400
        assert httpx2.get(url + "/health", trust_env=False).status_code == 401
        assert (
            httpx2.get(
                url + "/health", headers={"Authorization": "Bearer " + "0" * 64}, trust_env=False
            ).status_code
            == 401
        )
        backend.stop()
        backend.logs.join(timeout=2)
        assert b"nao_registrar" not in backend.diagnostics
        leaked = (
            backend.authorization.token.encode() in backend.diagnostics
            or backend.authorization.secret in backend.diagnostics
        )
        assert not leaked, "Diagnóstico não pode conter autorização."


def test_occupied_port_fails_without_stopping_existing_server(tmp_path: Path):
    with PrivateBackend(tmp_path / "first") as first:
        url = first.ready()
        with PrivateBackend(tmp_path / "second", port=int(url.rsplit(":", 1)[1])) as second:
            second.next(Kind.HELLO)
            assert struct.unpack("<H", second.next(Kind.ERROR))[0] == Code.STARTUP
            assert second.process.wait(timeout=10) != 0
        assert (
            httpx2.get(url + "/health", headers=first.headers, trust_env=False).status_code == 200
        )


@pytest.mark.parametrize(
    "arguments", [["--port", "-1"], ["--port", "65536"], ["--port", "abc"], ["--host", "0.0.0.0"]]
)
def test_invalid_startup_arguments_are_rejected(arguments):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "app", *arguments], capture_output=True, timeout=10
    )
    assert result.returncode == 2


def test_importing_entry_point_does_not_start_a_server():
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import app.__main__"], capture_output=True, timeout=10
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""


def test_no_context_fails_without_serving_http():
    result = subprocess.run(
        [sys.executable, "-B", "-m", "app"], input=b"", capture_output=True, timeout=10
    )
    assert result.returncode != 0
    assert result.stdout == b""


@pytest.mark.parametrize("kind", [Kind.INIT, Kind.READY, Kind.HELLO])
def test_out_of_order_command_closes_backend(tmp_path, kind):
    from app.runtime.control_protocol import SIZES

    with PrivateBackend(tmp_path / "db") as backend:
        backend.ready()
        payload = backend.authorization.execution_id + bytes(SIZES[kind] - 16)
        write_frame(backend.process.stdin, kind, payload)
        assert struct.unpack("<H", backend.next(Kind.ERROR))[0] == Code.PROTOCOL
        assert backend.process.wait(timeout=10) != 0


def test_closed_supervisor_pipe_stops_backend(tmp_path):
    with PrivateBackend(tmp_path / "db") as backend:
        backend.ready()
        backend.process.stdin.close()
        backend.next(Kind.ERROR)
        assert backend.process.wait(timeout=10) != 0


def test_new_backend_rejects_previous_secret(tmp_path):
    with PrivateBackend(tmp_path / "db") as first:
        first.ready()
        old_headers = first.headers
        first.stop()
    with PrivateBackend(tmp_path / "db") as second:
        url = second.ready()
        different = second.headers != old_headers
        assert different, "Nova execução exige novo segredo."
        assert httpx2.get(url + "/health", headers=old_headers, trust_env=False).status_code == 401


def test_duplicate_stop_is_protocol_error(tmp_path):
    with PrivateBackend(tmp_path / "db") as backend:
        backend.ready()
        frame_payload = backend.authorization.execution_id
        write_frame(backend.process.stdin, Kind.STOP, frame_payload)
        write_frame(backend.process.stdin, Kind.STOP, frame_payload)
        backend.next(Kind.ERROR)
        assert backend.process.wait(timeout=10) != 0
