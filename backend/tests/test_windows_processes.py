"""Processos, SQLite e ACLs reais; descoberta por pipe, sem leitura de logs."""

import csv
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from helpers.private_backend import PrivateBackend

from app.persistencia.errors import DatabaseAccessError, LockAccessError
from app.persistencia.instance_lock import InstanceLock
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import read_revision
from app.runtime.control_protocol import Code, Kind

HELPERS = Path(__file__).resolve().parent / "helpers"


def test_two_real_backends_cannot_use_same_database(settings):
    with PrivateBackend(settings.root) as first:
        url = first.ready()
        original = settings.database_path.read_bytes()
        with PrivateBackend(settings.root) as second:
            second.next(Kind.HELLO)
            second.next(Kind.PREPARING)
            assert struct.unpack("<H", second.next(Kind.ERROR))[0] == Code.INSTANCE_IN_USE
            assert second.process.wait(timeout=10) != 0
            second.logs.join(timeout=2)
            assert str(settings.database_path).encode() not in second.diagnostics
        assert settings.database_path.read_bytes() == original
        assert (
            httpx2.get(url + "/health", headers=first.headers, trust_env=False).status_code == 200
        )


def test_abrupt_process_death_releases_lock(settings):
    with PrivateBackend(settings.root) as backend:
        backend.ready()
        backend.kill_backend()
        backend.process.wait(timeout=5)
    assert settings.database_path.with_suffix(".instance.lock").exists()
    with PrivateBackend(settings.root) as backend:
        backend.ready()
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"


def test_cooperative_shutdown_runs_lifespan_and_releases_lock(settings):
    with PrivateBackend(settings.root) as backend:
        backend.ready()
        backend.stop()  # STOPPED é emitido somente após o retorno do lifespan.
    lock = InstanceLock(settings.database_path)
    lock.acquire()
    lock.release()
    with PrivateBackend(settings.root) as backend:
        backend.ready()


@pytest.mark.parametrize("abrupt", [False, True])
def test_real_authentication_reopen_and_revocation(settings, abrupt):
    password = "frase ficticia para ensaio Windows 493!"
    with PrivateBackend(settings.root) as backend:
        url = backend.ready()
        with httpx2.Client(base_url=url, headers=backend.headers, trust_env=False) as client:
            assert client.get("/auth/configuracao-inicial").json() == {"necessaria": True}
            created = client.post(
                "/auth/configuracao-inicial",
                json={
                    "nome": "Pessoa Fictícia",
                    "login": "teste.local",
                    "senha": password,
                },
            )
            assert created.status_code == 201
            result = client.post("/auth/login", json={"login": "teste.local", "senha": password})
            assert result.status_code == 200
            token = result.json()["token"]
            assert (
                client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code == 200
            )
            # Logout real e nova sessão, preservada até a queda/fechamento.
            assert (
                client.post("/auth/logout", headers={"X-eDisk-Session": token}).status_code == 204
            )
            assert (
                client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code == 401
            )
            token = client.post(
                "/auth/login", json={"login": "teste.local", "senha": password}
            ).json()["token"]
        if abrupt:
            backend.kill_backend()
            backend.process.wait(timeout=5)
        else:
            backend.stop()
        backend.logs.join(timeout=2)
        assert password.encode() not in backend.diagnostics
        assert token.encode() not in backend.diagnostics
    with PrivateBackend(settings.root) as backend:
        url = backend.ready()
        with httpx2.Client(base_url=url, headers=backend.headers, trust_env=False) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/auth/configuracao-inicial").json() == {"necessaria": False}
            assert (
                client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code == 401
            )
            assert (
                client.post(
                    "/auth/login", json={"login": "teste.local", "senha": password}
                ).status_code
                == 200
            )
        backend.stop()


def test_distinct_test_databases_can_run_simultaneously(tmp_path):
    with PrivateBackend(tmp_path / "a") as first, PrivateBackend(tmp_path / "b") as second:
        assert first.ready() != second.ready()


def test_real_backend_starts_from_unrelated_directory_with_spaces(settings, tmp_path):
    elsewhere = tmp_path / "outro diretorio com espaços"
    elsewhere.mkdir()
    with PrivateBackend(settings.root, cwd=elsewhere) as backend:
        backend.ready()
        assert list(elsewhere.iterdir()) == []
        assert settings.database_path.exists()


def test_abrupt_exit_rolls_back_uncommitted_transaction(settings, tmp_path):
    persistence = Persistence(settings)
    persistence.start()
    persistence.close()
    log_path = tmp_path / "writer.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-B", str(HELPERS / "uncommitted_writer.py")],
            env={**os.environ, "EDISK_ENV": "test", "EDISK_TEST_ROOT": str(settings.root)},
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if "uncommitted" in log_path.read_text(encoding="utf-8"):
                    break
                assert process.poll() is None
                time.sleep(0.03)
            else:
                pytest.fail("Transação de teste não iniciou")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
    with PrivateBackend(settings.root) as backend:
        backend.ready()
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"


def test_import_does_not_create_database_or_directories(settings, tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import app.__main__; import app.main"],
        cwd=tmp_path,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""
    assert not settings.root.exists()


def sid():
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True
    )
    return next(csv.reader([result.stdout.strip()]))[1]


def test_windows_acl_denial_is_not_reported_as_instance_conflict(settings):
    settings.data_dir.mkdir(parents=True)
    identity = sid()
    path = settings.database_path.with_suffix(".instance.lock")
    path.touch()
    subprocess.run(
        ["icacls", str(path), "/deny", f"*{identity}:(W)"], capture_output=True, check=True
    )
    try:
        with pytest.raises(PermissionError):
            path.open("r+b")
        with pytest.raises(LockAccessError):
            InstanceLock(settings.database_path).acquire()
    finally:
        subprocess.run(
            ["icacls", str(path), "/remove:d", f"*{identity}"], capture_output=True, check=True
        )
    lock = InstanceLock(settings.database_path)
    lock.acquire()
    lock.release()


def test_windows_directory_acl_prevents_startup(settings):
    settings.root.mkdir(parents=True)
    identity = sid()
    subprocess.run(
        ["icacls", str(settings.root), "/deny", f"*{identity}:(W)"], capture_output=True, check=True
    )
    persistence = Persistence(settings)
    try:
        with pytest.raises(PermissionError):
            settings.data_dir.mkdir()
        with pytest.raises(DatabaseAccessError):
            persistence.start()
        assert not persistence.probe()
        assert not settings.database_path.exists()
    finally:
        subprocess.run(
            ["icacls", str(settings.root), "/remove:d", f"*{identity}"],
            capture_output=True,
            check=True,
        )
    persistence.start()
    persistence.close()


def test_private_startup_reports_persistence_failure(settings):
    settings.root.mkdir(parents=True)
    settings.data_dir.write_text("bloqueio", encoding="utf-8")
    with PrivateBackend(settings.root) as backend:
        backend.next(Kind.HELLO)
        backend.next(Kind.PREPARING)
        assert struct.unpack("<H", backend.next(Kind.ERROR))[0] == Code.PERSISTENCE
        assert backend.process.wait(timeout=10) != 0
