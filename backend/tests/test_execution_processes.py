"""Ensaios Windows reais, Job nativo, Uvicorn e SQLite descartavel."""

import importlib.util
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest
import test_execucoes_service as b1
from helpers.private_backend import PrivateBackend

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "native_supervisor_tests", ROOT / "scripts/tests/test_supervisor.py"
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
HELPER = Path(__file__).parent / "helpers/integrated_executor.py"

cadastro = b1.cadastro
consentimento = b1.consentimento
rascunho = b1.rascunho
pronta = b1.pronta
simulavel = b1.simulavel
confirmada = b1.confirmada
autorizada = b1.autorizada


@pytest.fixture
def preparada(autorizada, tmp_path):
    f = autorizada
    b1.iniciar(f)
    root = tmp_path / "processo integrado"
    root.mkdir()
    destination = root / "data root/data/edisk.sqlite3"
    destination.parent.mkdir(parents=True)
    with (
        closing(sqlite3.connect(f["app"].state.persistence.settings.database_path)) as source,
        closing(sqlite3.connect(destination)) as target,
    ):
        source.backup(target)
    return root, destination


def wait_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            pytest.fail("Checkpoint de processo nao observado.")
        time.sleep(0.01)


def dados(path):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        return (
            conn.execute(
                "SELECT estado,invocacao_em FROM tentativas_envio ORDER BY numero"
            ).fetchall(),
            conn.execute("SELECT estado FROM campanha_execucoes").fetchone()[0],
        )


@pytest.mark.parametrize(
    "fase", ["reserva", "marcador", "mock", "resposta", "commit_resultado", "resultado"]
)
def test_terminateprocess_e_reabertura_sem_retomada(preparada, fase):
    root, db = preparada
    with native.Harness(root, fake=str(root), script=HELPER, extra=(fase,)) as harness:
        ready = harness.wait("ready")
        wait_file(root / (fase + ".ready"))
        process = native.ProcessHandle(ready["backend"])
        try:
            process.kill()
            event = harness.wait("exited")
            assert event["active"] == 0 and process.dead()
        finally:
            process.close()
    with PrivateBackend(root / "data root") as backend:
        backend.ready()
        rows, execution = dados(db)
        assert len(rows) == 1
        esperado = {"reserva": "interrompida", "resultado": "aceita_provedor"}.get(
            fase, "desconhecida"
        )
        assert rows[0][0] == esperado
        assert execution == ("concluida" if fase == "resultado" else "pausada")
        backend.stop()
    with PrivateBackend(root / "data root") as backend:
        backend.ready()
        assert dados(db) == (rows, execution)
        backend.stop()


def test_evento_fecha_admissao_antes_de_stop_e_ack_dart(preparada):
    root, db = preparada
    with native.Harness(root, fake=str(root), script=HELPER, extra=("marcador",)) as harness:
        harness.wait("ready")
        wait_file(root / "marcador.ready")
        harness.process.stdin.write("quiesce\n")
        harness.process.stdin.flush()
        # Esperar reconhecimento do pipe sem liberar a chamada: nao usar HTTP
        # ou polling como gatilho de execucao; aqui so observamos o banco.
        wait_file(root / "barreira.ready")
        (root / "release").touch()
        deadline = time.monotonic() + 10
        while dados(db)[1] != "concluida_com_falhas" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not (root / "mock.ready").exists()
        assert dados(db)[0][0][0] == "desconhecida"
        assert not harness.stop()["forced"]


def test_stop_durante_mock_drena_resultado_antes_do_banco(preparada):
    root, db = preparada
    with native.Harness(root, fake=str(root), script=HELPER, extra=("mock",)) as harness:
        harness.wait("ready")
        wait_file(root / "mock.ready")
        harness.process.stdin.write("stop\n")
        harness.process.stdin.flush()
        harness.wait("stopping")
        (root / "release").touch()
        event = harness.wait("stopped")
        assert not event["forced"] and event["active"] == 0
        assert dados(db)[0][0][0] == "aceita_provedor"


def test_stop_excede_drenagem_forca_falha_e_recupera_desconhecida(preparada):
    root, db = preparada
    with native.Harness(root, fake=str(root), script=HELPER, extra=("mock",)) as harness:
        harness.wait("ready")
        wait_file(root / "mock.ready")
        result = harness.stop()
        assert result["forced"] and result["active"] == 0
    with PrivateBackend(root / "data root") as backend:
        backend.ready()
        assert dados(db)[0][0][0] == "desconhecida"
        backend.stop()


@pytest.mark.parametrize("erro", ["eof", "protocolo"])
def test_canal_real_falha_fecha_backend_sem_rearmar(preparada, erro):
    root, _ = preparada
    with PrivateBackend(root / "data root") as backend:
        backend.ready()
        if erro == "eof":
            backend.process.stdin.close()
        else:
            backend.process.stdin.write(b"INVALIDO")
            backend.process.stdin.flush()
        from app.runtime.control_protocol import Kind

        backend.next(Kind.ERROR)
        assert backend.process.wait(timeout=10) != 0


@pytest.mark.parametrize("abrupto", [False, True])
def test_flutter_debug_real_fechamento_e_queda(preparada, monkeypatch, abrupto):
    root, db = preparada
    monkeypatch.syspath_prepend(str(ROOT / "scripts/tests"))
    spec = importlib.util.spec_from_file_location(
        "windows_app_tests", ROOT / "scripts/tests/test_windows_app.py"
    )
    windows = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(windows)
    exe = ROOT / "frontend/build/windows/x64/runner/Debug/edisk.exe"
    process = subprocess.Popen(
        [str(exe)],
        cwd=root,
        env={
            **os.environ,
            "EDISK_DEV_ROOT": str(ROOT),
            "EDISK_ENV": "test",
            "EDISK_TEST_ROOT": str(root / "data root"),
        },
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    handles = []
    try:
        hwnd, _ = windows.wait_for(lambda: windows.window_for({process.pid}))
        windows.wait_for(lambda: windows.listening_backend(native.descendants(process.pid)))
        handles = [native.ProcessHandle(pid) for pid in native.descendants(process.pid)]
        if abrupto:
            process.kill()
        else:
            windows.USER.PostMessageW(hwnd, 0x0010, 0, 0)
        assert process.wait(timeout=16) == (1 if abrupto else 0)
        assert all(handle.dead() for handle in handles)
        assert dados(db)[0] == []  # Startup da janela nao ativa o executor.
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for handle in handles:
            handle.close()
