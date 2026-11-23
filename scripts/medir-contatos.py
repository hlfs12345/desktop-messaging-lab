"""Ensaio reproduzível, somente com dados fictícios em raiz temporária."""

import io
import json
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
import secrets  # noqa: E402

from app.auth.schemas import BootstrapInput, LoginInput  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.instance_auth import InstanceAuthorization  # noqa: E402
from app.main import create_app  # noqa: E402


def main():
    rows = [(f"Contato fictício {i:05}", f"+55119{10000000 + i:08}") for i in range(8000)]
    csv = ("nome;telefone\n" + "\n".join(";".join(row) for row in rows)).encode()
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Contatos")
    sheet.append(["nome", "telefone"])
    for row in rows:
        sheet.append(row)
    target = io.BytesIO()
    book.save(target)
    book.close()
    reports = []
    for _iteration in range(3):
        with tempfile.TemporaryDirectory(prefix="edisk-cadastro-") as temp:
            authorization = InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))
            app = create_app(
                Settings("test", Path(temp) / "app"),
                authorization=authorization,
            )
            with TestClient(app, base_url="http://127.0.0.1") as client:
                auth = app.state.auth
                user = auth.bootstrap(
                    BootstrapInput(
                        nome="Fictício", login="teste", senha="frase fictícia para desempenho 489!"
                    )
                )
                token = auth.login(
                    LoginInput(login="teste", senha="frase fictícia para desempenho 489!")
                ).token
                service = app.state.importacoes
                client.headers.update(
                    {"Authorization": f"Bearer {authorization.token}", "X-eDisk-Session": token}
                )
                from app.importacoes.schemas import ConfiguracaoImportacao

                measured = {}
                for name, payload in [("csv", csv), ("xlsx", target.getvalue())]:
                    identifier = service.criar("contatos." + name, user.id)["id"]
                    config = ConfiguracaoImportacao(
                        coluna_nome=0, coluna_telefone=1, aba="Contatos" if name == "xlsx" else None
                    )
                    start = time.perf_counter()
                    service.carregar(identifier, user.id, payload)
                    service.validar(identifier, user.id, config)
                    service.future.result(timeout=60)
                    elapsed = time.perf_counter() - start
                    state = service.obter(identifier)
                    assert state["status"] == "pronta", state
                    assert (
                        state["contagens"].get("inserido" if name == "csv" else "sem_alteracao")
                        == 8000
                    )
                    start = time.perf_counter()
                    result = service.confirmar(identifier, user.id, token)
                    commit = time.perf_counter() - start
                    assert result["status"] == "confirmada"
                    measured[name] = {
                        "validacao_s": elapsed,
                        "confirmacao_s": commit,
                        "bytes": len(payload),
                        "contagens": result["contagens"],
                    }
                latencies = []
                for i in range(40):
                    start = time.perf_counter()
                    app.state.contatos.listar(q=f"{i:02}", pagina=1, limite=50)
                    latencies.append((time.perf_counter() - start) * 1000)
                measured["busca_p95_ms"] = sorted(latencies)[37]
                latencies = []
                for i in range(40):
                    start = time.perf_counter()
                    response = client.get("/contatos", params={"q": f"{i:02}", "limite": 50})
                    assert response.status_code == 200
                    latencies.append((time.perf_counter() - start) * 1000)
                measured["api_busca_p95_ms"] = sorted(latencies)[37]
                tracemalloc.start()
                identifier = service.criar("memoria.csv", user.id)["id"]
                service.carregar(identifier, user.id, csv)
                service.validar(
                    identifier, user.id, ConfiguracaoImportacao(coluna_nome=0, coluna_telefone=1)
                )
                service.future.result(timeout=90)
                measured["pico_python_previa_mib"] = tracemalloc.get_traced_memory()[1] / 1024**2
                tracemalloc.stop()
                service.cancelar(identifier, user.id)
                measured["total_contatos"] = app.state.contatos.listar()["total"]
                reports.append(measured)
    output = {
        "python": sys.version,
        "windows": platform.platform(),
        "processador": platform.processor(),
        "registros": 8000,
        "execucoes": reports,
        "medianas": {
            name: {
                key: statistics.median(r[name][key] for r in reports)
                for key in ["validacao_s", "confirmacao_s"]
            }
            for name in ["csv", "xlsx"]
        },
        "memoria": "Pico de alocações Python da reimportação, tracemalloc; "
        "não representa RSS total.",
    }
    dest = ROOT / "build/validation/entrega9-desempenho.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
