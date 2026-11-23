"""D2: fluxo HTTP completo em backend/supervisor Windows reais e base descartavel."""

import json
import sqlite3
import time
from contextlib import closing

import httpx2
import test_execution_processes as processos
import test_executor_mock_desempenho as volume
from helpers.private_backend import PrivateBackend

cadastro = processos.cadastro
consentimento = processos.consentimento
rascunho = processos.rascunho
pronta = processos.pronta
simulavel = processos.simulavel


def test_fluxo_http_integrado_completo_reabre_sem_ativar(simulavel, tmp_path):
    volume.popular(simulavel, 3)
    r, cid, _ = simulavel
    root = tmp_path / "fluxo HTTP integrado"
    root.mkdir()
    db = root / "data root/data/edisk.sqlite3"
    db.parent.mkdir(parents=True)
    with (
        closing(sqlite3.connect(r[1].state.persistence.settings.database_path)) as source,
        closing(sqlite3.connect(db)) as target,
    ):
        source.backup(target)
    helper = processos.ROOT / "backend/tests/helpers/execution_http_flow.py"
    with processos.native.Harness(root, fake=str(root), script=helper, extra=(cid,)) as harness:
        harness.wait("ready")
        deadline = time.monotonic() + 60
        while not (root / "flow.done").exists():
            assert not (root / "flow.failed").exists(), "Fluxo HTTP real falhou."
            assert time.monotonic() < deadline, "Fluxo HTTP real excedeu o prazo."
            time.sleep(0.02)
        result = json.loads((root / "flow.json").read_text(encoding="utf-8"))
        assert result["estado"] == "concluida" and result["tentativas"] == 4
        stamp = time.perf_counter()
        event = harness.stop()
        fechamento = time.perf_counter() - stamp
        assert event["active"] == 0 and not event["forced"]
    with PrivateBackend(root / "data root") as backend:
        with httpx2.Client(base_url=backend.ready(), headers=backend.headers, timeout=10) as client:
            session = client.post(
                "/auth/login",
                json={
                    "login": "teste",
                    "senha": "frase fictícia de teste 489!",
                },
            ).json()
            client.headers["x-edisk-session"] = session["token"]
            for _ in range(3):
                e = client.get(f"/campanhas/{cid}/execucoes/{result['execucao_id']}").json()
                assert e["estado"] == "concluida" and not e["operacional"]["worker_ativo"]
                assert e["contagens"]["aceita_provedor"] == 3
        backend.stop()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT count(*) FROM tentativas_envio").fetchone()[0] == 4
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    print(f"\nD2 HTTP real: pausa {result['pausa_segundos']:.4f}s; fechamento {fechamento:.4f}s")
