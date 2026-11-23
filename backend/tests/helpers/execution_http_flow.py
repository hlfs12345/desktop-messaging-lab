"""Ensaio HTTP privado em processo/Job reais; nunca integra o bundle."""

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx2

from app.campanhas.executor_mock import ExecutorMock
from app.runtime import launcher

folder, cid = Path(sys.argv[1]), sys.argv[2]
reserva, liberar = Event(), Event()
original_create, original_serve = launcher.create_app, launcher.IntegratedServer.serve
original_reservar = ExecutorMock._reservar
authorization = None
primeira = True


def reservar(self, adm):
    global primeira
    result = original_reservar(self, adm)
    if primeira:
        primeira = False
        reserva.set()
        if not liberar.wait(20):
            raise RuntimeError("Checkpoint de pausa expirou.")
    return result


def criar(**kwargs):
    global authorization
    authorization = kwargs["authorization"]
    return original_create(**kwargs)


def esperar(client, root, estado):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(root)
        assert response.status_code == 200
        if response.json()["estado"] == estado:
            return response.json()
        time.sleep(0.02)
    raise AssertionError("Estado operacional esperado nao observado.")


def fluxo(port):
    with httpx2.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Authorization": "Bearer " + authorization.token},
        timeout=15,
    ) as client:

        def get(path, **kwargs):
            response = client.get(path, **kwargs)
            assert response.status_code == 200
            return response.json()

        def post(path, body, expected=200):
            response = client.post(path, json=body)
            assert response.status_code == expected
            return response.json()

        login = post("/auth/login", {"login": "teste", "senha": "frase fictícia de teste 489!"})
        client.headers["x-edisk-session"] = login["token"]
        root = "/campanhas/" + cid
        assert get(root + "/execucoes")["total"] == 0
        r = get(root + "/revisao-preparacao", params={"cenario": "aceita_provedor"})
        prep = post(
            root + "/preparacoes",
            dict(
                operacao_id=str(uuid4()),
                versao=r["versao"],
                marcador=r["marcador"],
                configuracao_simulacao=r["configuracao_simulacao"],
            ),
            201,
        )
        pr = root + "/preparacoes/" + prep["preparacao_id"]
        photo = get(pr)
        post(
            pr + "/confirmacao",
            dict(
                operacao_id=str(uuid4()),
                versao=prep["versao"],
                preparacao_id=prep["preparacao_id"],
                impressao=photo["impressao"],
            ),
        )
        r = get(pr + "/revisao-autorizacao")
        post(
            pr + "/autorizacoes-simulacao",
            dict(
                operacao_id=str(uuid4()),
                versao=r["versao"],
                impressao=r["impressao"],
                resumo_configuracao=r["resumo_configuracao"],
                marcador_autorizacao=r["marcador_autorizacao"],
                confirmo_simulacao=True,
            ),
            201,
        )
        r = get(pr + "/revisao-inicio")
        receipt = post(
            pr + "/execucoes",
            {
                k: r[k]
                for k in (
                    "versao",
                    "autorizacao_id",
                    "versao_autorizacao",
                    "impressao",
                    "resumo_configuracao",
                    "marcador_inicio",
                )
            }
            | {"operacao_id": str(uuid4()), "confirmo_inicio": True},
            201,
        )
        assert receipt["ativacao"]["admissao_confirmada"]
        eid = receipt["recibo"]["execucao_id"]
        er = root + "/execucoes/" + eid
        assert reserva.wait(10)
        e = get(er)
        start = time.perf_counter()
        pause = post(er + "/pausa", dict(operacao_id=str(uuid4()), versao=e["versao"]))
        latency = time.perf_counter() - start
        assert pause["atual"]["estado"] == "pausando"
        liberar.set()
        esperar(client, er, "pausada")
        r = get(er + "/revisao-retomada")
        assert r["pendentes"] == r["elegiveis_pendentes"] == 3
        post(
            er + "/retomada",
            {
                k: r[k]
                for k in (
                    "versao",
                    "preparacao_id",
                    "autorizacao_id",
                    "versao_autorizacao",
                    "geracao",
                    "impressao",
                    "resumo_configuracao",
                    "marcador_retomada",
                )
            }
            | {"operacao_id": str(uuid4()), "confirmo_retomada": True},
        )
        e = esperar(client, er, "concluida")
        items = get(er + "/itens")["itens"]
        attempts = sum(get(er + "/itens/" + i["id"] + "/tentativas")["total"] for i in items)
        assert e["contagens"]["aceita_provedor"] == 3 and attempts == 4
        (folder / "flow.json").write_text(
            json.dumps(
                dict(
                    estado=e["estado"],
                    execucao_id=eid,
                    total=len(items),
                    tentativas=attempts,
                    pausa_segundos=latency,
                    geracao=e["geracao"],
                )
            ),
            encoding="utf-8",
        )
        (folder / "flow.done").touch()


async def servir(self, sockets=None):
    async def drive():
        while not self.started:
            await asyncio.sleep(0.01)
        port = self.servers[0].sockets[0].getsockname()[1]
        try:
            await asyncio.to_thread(fluxo, port)
        except Exception:
            (folder / "flow.failed").touch()
            raise

    driver = asyncio.create_task(drive())
    try:
        await original_serve(self, sockets=sockets)
    finally:
        liberar.set()
        driver.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await driver


ExecutorMock._reservar = reservar
launcher.create_app = criar
launcher.IntegratedServer.serve = servir
raise SystemExit(launcher.run())
