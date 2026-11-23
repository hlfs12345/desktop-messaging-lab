"""Prontidão, falha sanitizada e fechamento do lifespan."""

import ctypes
from ctypes import wintypes

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.persistencia.instance_lock import InstanceLock


def test_health_without_lifespan_is_not_ready(settings: Settings, authorization) -> None:
    application = create_app(settings, authorization=authorization)
    client = TestClient(
        application,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {authorization.token}"},
    )
    assert client.get("/health").status_code == 503
    assert not settings.root.exists()


def test_lifespan_closes_database_and_releases_lock(settings: Settings, authorization) -> None:
    application = create_app(settings, authorization=authorization)
    with TestClient(
        application,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {authorization.token}"},
    ) as client:
        assert client.get("/health").status_code == 200
        persistence = application.state.persistence
        assert persistence.probe()
    assert not persistence.probe()
    assert persistence.database.closed
    lock = InstanceLock(settings.database_path)
    lock.acquire()
    lock.release()


def test_startup_error_is_sanitized_and_never_ready(settings: Settings, authorization) -> None:
    settings.root.mkdir(parents=True)
    settings.data_dir.write_text("arquivo bloqueando diretorio", encoding="utf-8")
    application = create_app(settings, authorization=authorization)
    with pytest.raises(RuntimeError) as raised, TestClient(application):
        pass
    assert str(settings.root) not in str(raised.value)
    assert not hasattr(application.state, "persistence")
    assert not settings.database_path.exists()


def test_health_reports_real_file_access_failure(settings: Settings, authorization) -> None:
    application = create_app(settings, authorization=authorization)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    with TestClient(
        application,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {authorization.token}"},
    ) as client:
        handle = kernel.CreateFileW(str(settings.database_path), 0x80000000, 0, None, 3, 0, None)
        assert handle != ctypes.c_void_p(-1).value
        try:
            response = client.get("/health")
            assert response.status_code == 503
            assert response.json() == {"status": "indisponivel"}
            assert str(settings.root) not in response.text
        finally:
            assert kernel.CloseHandle(handle)
        assert client.get("/health").status_code == 200


def test_missing_database_is_not_recreated_by_health(settings: Settings, authorization) -> None:
    application = create_app(settings, authorization=authorization)
    with TestClient(
        application,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {authorization.token}"},
    ) as client:
        saved = settings.database_path.with_suffix(".saved")
        settings.database_path.rename(saved)
        try:
            assert client.get("/health").status_code == 503
            assert not settings.database_path.exists()
        finally:
            saved.rename(settings.database_path)
        assert client.get("/health").status_code == 200
