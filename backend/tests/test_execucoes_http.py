"""D1 HTTP real com lifecycle/worker e SQLite descartavel; mock puro sem rede."""

import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Event
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_execucoes_service as b1
import test_execution_lifecycle as c
import test_executor_mock as b2
import test_executor_mock_desempenho as volume
from helpers.runtime_lifetime import SimulatedLifetime
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError

from app.auth.models import Usuario
from app.campanhas import preparacoes as preparacoes_mod
from app.campanhas.executor_mock import ExecutorMock
from app.contatos.errors import NegocioError

cadastro = b1.cadastro
consentimento = b1.consentimento
rascunho = b1.rascunho
pronta = b1.pronta
simulavel = b1.simulavel
confirmada = b1.confirmada
autorizada = b1.autorizada


@pytest.fixture
def http(autorizada):
    f = {**autorizada, "svc": autorizada["app"].state.execucoes}
    f["url"] = f["root"] + "/preparacoes/" + f["pid"]
    f["rt"] = f["app"].state.execution_runtime
    return f


def integrar(f):
    lifetime = SimulatedLifetime()
    f["rt"].lifetime = lifetime
    lifetime.subscribe(f["rt"].close_admission)
    return lifetime


def corpo_inicio(f):
    r = f["c"].get(f["url"] + "/revisao-inicio")
    assert r.status_code == 200, r.text
    return b1.entrada(f, r.json()).model_dump(mode="json")


def iniciar(f, body=None):
    response = f["c"].post(f["url"] + "/execucoes", json=body or corpo_inicio(f))
    assert response.status_code == 201, response.text
    result = response.json()
    f["eid"] = result["recibo"]["execucao_id"]
    f["eurl"] = f["root"] + "/execucoes/" + f["eid"]
    return result


def corpo_retomada(f):
    response = f["c"].get(f["eurl"] + "/revisao-retomada")
    assert response.status_code == 200, response.text
    r = response.json()
    return {
        k: v
        for k, v in r.items()
        if k
        in {
            "versao",
            "geracao",
            "preparacao_id",
            "autorizacao_id",
            "versao_autorizacao",
            "impressao",
            "resumo_configuracao",
            "marcador_retomada",
        }
    } | {"operacao_id": str(uuid4()), "confirmo_retomada": True}


def rotas(f):
    eid, iid = str(uuid4()), str(uuid4())
    root = f["root"] + "/execucoes/" + eid
    return [
        ("GET", f["url"] + "/revisao-inicio"),
        ("POST", f["url"] + "/execucoes"),
        ("GET", f["root"] + "/execucoes"),
        ("GET", root),
        ("GET", root + "/itens"),
        ("GET", root + "/itens/" + iid + "/tentativas"),
        ("POST", root + "/pausa"),
        ("GET", root + "/revisao-retomada"),
        ("POST", root + "/retomada"),
    ]


def test_todas_rotas_exigem_sessao_e_instancia_antes_do_parser(http):
    f = http
    for method, url in rotas(f):
        sem_sessao = dict(f["c"].headers)
        sem_sessao["x-edisk-session"] = "invalida"
        assert f["c"].request(method, url, headers=sem_sessao, content=b"{").status_code == 401
        assert (
            f["c"].request(method, url, headers={"Authorization": "Bearer invalida"}).status_code
            == 401
        )


@pytest.mark.parametrize("perfil", ["administrador", "operador"])
def test_perfis_legitimos_e_gets_nao_processam(http, perfil):
    f = http
    with f["svc"].repo.transacao() as s:
        s.execute(update(Usuario).values(perfil=perfil))
    integrar(f)
    for _ in range(3):
        review = f["c"].get(f["url"] + "/revisao-inicio")
        assert review.status_code == 200 and review.json()["processamento_disponivel"]
        assert review.json()["configuracao_simulacao"]["provedor"] == "mock"
        assert f["c"].get(f["root"] + "/execucoes").json()["total"] == 0
    assert b2.linhas(f, "tentativas_envio") == [] and f["rt"]._active is None


def test_perfil_sem_permissao(http, monkeypatch):
    f = http
    inicio = corpo_inicio(f)
    original = f["app"].state.auth.current

    def current(*args, **kwargs):
        result = original(*args, **kwargs)
        return SimpleNamespace(usuario=SimpleNamespace(id=result.usuario.id, perfil="consulta"))

    monkeypatch.setattr(f["app"].state.auth, "current", current)
    for method, url in rotas(f):
        if method == "GET":
            assert f["c"].get(url).status_code == 403
        else:
            if url.endswith("/execucoes"):
                data = inicio
            elif url.endswith("/pausa"):
                data = {"operacao_id": str(uuid4()), "versao": 1}
            else:
                data = {
                    k: v
                    for k, v in inicio.items()
                    if k not in {"marcador_inicio", "confirmo_inicio"}
                } | {
                    "preparacao_id": f["pid"],
                    "geracao": 1,
                    "marcador_retomada": "ativar-v1." + "0" * 64,
                    "confirmo_retomada": True,
                }
            assert f["c"].post(url, json=data).status_code == 403


def test_inicio_persistido_sem_launcher_replay_nao_ativa_e_retomada_explicita(http, monkeypatch):
    f = http
    data = corpo_inicio(f)
    result = iniciar(f, data)
    assert not result["ativacao"]["admissao_confirmada"]
    assert result["ativacao"]["erro_codigo"] == "ativacao_indisponivel"
    assert result["recibo"]["estado_execucao"] == "executando"
    assert result["atual"]["estado"] == "pausada"
    assert not result["atual"]["operacional"]["worker_ativo"]
    integrar(f)
    replay = iniciar(f, data)
    assert replay["replay"] and not replay["ativacao"]["solicitada"]
    assert replay["recibo"] == result["recibo"]
    assert b2.linhas(f, "tentativas_envio") == []
    retomar = corpo_retomada(f)
    response = f["c"].post(f["eurl"] + "/retomada", json=retomar)
    assert response.status_code == 200, response.text
    assert response.json()["ativacao"]["admissao_confirmada"]
    assert response.json()["atual"]["geracao"] > result["atual"]["geracao"]
    c.esperar(f["rt"])
    assert f["c"].get(f["eurl"]).json()["estado"] == "concluida"
    monkeypatch.setattr(f["rt"], "activate", lambda *a, **kw: pytest.fail("Replay ativou"))
    response = f["c"].post(f["eurl"] + "/retomada", json=retomar)
    assert response.status_code == 200 and response.json()["replay"]
    assert len(b2.linhas(f, "tentativas_envio")) == 1


def test_inicio_explicito_worker_real_sem_io_externo(http, monkeypatch):
    f = http
    integrar(f)

    def proibido(*args, **kwargs):
        pytest.fail("I/O externo no comando mock")

    monkeypatch.setattr(socket, "create_connection", proibido)
    monkeypatch.setattr(subprocess, "Popen", proibido)
    result = iniciar(f)
    assert result["ativacao"]["admissao_confirmada"]
    c.esperar(f["rt"])
    e = f["c"].get(f["eurl"]).json()
    assert e["estado"] == "concluida" and e["contagens"]["aceita_provedor"] == 1
    itens = f["c"].get(f["eurl"] + "/itens").json()
    iid = itens["itens"][0]["id"]
    tentativas = f["c"].get(f["eurl"] + "/itens/" + iid + "/tentativas").json()
    assert tentativas["total"] == 1
    assert tentativas["itens"][0]["codigo_resultado"] == "aceita"
    assert tentativas["itens"][0]["invocacao_em"] is not None


@pytest.mark.parametrize(
    "alteracao", ["marcador_inicio", "versao", "autorizacao_id", "confirmo_inicio", "extra"]
)
def test_inicio_adulterado_ou_obsoleto(http, alteracao):
    f = http
    body = corpo_inicio(f)
    if alteracao == "extra":
        body["extra"] = "secreto"
    elif alteracao == "confirmo_inicio":
        body[alteracao] = "true"
    elif alteracao == "versao":
        body[alteracao] += 1
    elif alteracao == "autorizacao_id":
        body[alteracao] = str(uuid4())
    else:
        body[alteracao] = "inicio-v1." + "0" * 64
    response = f["c"].post(f["url"] + "/execucoes", json=body)
    assert response.status_code in (409, 422)
    assert "secreto" not in response.text
    assert f["c"].get(f["root"] + "/execucoes").json()["total"] == 0


def test_uuid_divergente_inicio_e_pausa(http):
    f = http
    data = corpo_inicio(f)
    iniciar(f, data)
    for mudanca in ({"versao": data["versao"] + 1}, {"autorizacao_id": str(uuid4())}):
        assert f["c"].post(f["url"] + "/execucoes", json=data | mudanca).status_code == 409
    p = {"operacao_id": str(uuid4()), "versao": f["c"].get(f["eurl"]).json()["versao"]}
    r = f["c"].post(f["eurl"] + "/pausa", json=p)
    assert r.status_code == 200 and r.json()["atual"]["pausa_efetivada"]
    replay = f["c"].post(f["eurl"] + "/pausa", json=p).json()
    assert replay["replay"] and replay["recibo"] == r.json()["recibo"]
    assert (
        f["c"].post(f["eurl"] + "/pausa", json=p | {"versao": p["versao"] + 1}).status_code == 409
    )


@pytest.mark.parametrize("fase", ["antes", "depois"])
def test_falha_ativacao_retomada_recibo_atomico(http, monkeypatch, fase):
    f = http
    iniciar(f)
    lifetime = integrar(f)
    data = corpo_retomada(f)
    original = ExecutorMock.admitir

    def falhar(executor, *args, **kwargs):
        if fase == "antes":
            raise NegocioError("fotografia_indisponivel", "Indisponivel", 503)
        result = original(executor, *args, **kwargs)
        lifetime.close()
        return result

    monkeypatch.setattr(ExecutorMock, "admitir", falhar)
    response = f["c"].post(f["eurl"] + "/retomada", json=data)
    if fase == "antes":
        assert response.status_code == 503, response.text
        assert not any(a["id"] == data["operacao_id"] for a in b2.linhas(f, "auditoria"))
    else:
        assert response.status_code == 200, response.text
        assert response.json()["ativacao"]["erro_codigo"] == "ativacao_indisponivel"
        replay = f["c"].post(f["eurl"] + "/retomada", json=data)
        assert replay.json()["replay"]
    assert f["c"].get(f["eurl"]).json()["estado"] == "pausada"
    assert b2.linhas(f, "tentativas_envio") == []


def test_pausa_reserva_concorrente_persiste_intencao_nao_fabrica_resultado(http, monkeypatch):
    f = http
    integrar(f)
    reservado, liberar = Event(), Event()
    original = ExecutorMock._reservar

    def reservar(executor, adm):
        result = original(executor, adm)
        reservado.set()
        assert liberar.wait(10)
        return result

    monkeypatch.setattr(ExecutorMock, "_reservar", reservar)
    try:
        iniciar(f)
        assert reservado.wait(10)
        e = f["c"].get(f["eurl"]).json()
        response = f["c"].post(
            f["eurl"] + "/pausa", json={"operacao_id": str(uuid4()), "versao": e["versao"]}
        )
        assert response.status_code == 200, response.text
        atual = response.json()["atual"]
        assert atual["estado"] == "pausando" and atual["pausa_solicitada"]
        assert not atual["operacional"]["admitida"]
        assert b2.linhas(f, "tentativas_envio")[0]["estado"] == "reservada"
    finally:
        liberar.set()
    c.esperar(f["rt"])
    assert f["c"].get(f["eurl"]).json()["pausa_efetivada"]
    assert b2.linhas(f, "tentativas_envio")[0]["estado"] == "interrompida"


@pytest.mark.parametrize("pagina,limite", [(0, 20), (1, 51), (1000001, 20), (1, 0)])
def test_paginacao_limites(http, pagina, limite):
    f = http
    iniciar(f)
    for url in (f["root"] + "/execucoes", f["eurl"] + "/itens"):
        assert f["c"].get(url, params={"pagina": pagina, "limite": limite}).status_code == 422


def test_historico_sem_dpapi_paginado_sanitizado_sem_ativacao(http, monkeypatch):
    f = http
    iniciar(f)

    def proibido(*args, **kwargs):
        pytest.fail("Historico consultou fotografia/worker")

    monkeypatch.setattr(preparacoes_mod, "abrir", proibido)
    monkeypatch.setattr(f["svc"].p, "_autenticar_fotografia", proibido)
    monkeypatch.setattr(f["rt"], "activate", proibido)
    itens = f["c"].get(f["eurl"] + "/itens").json()
    iid = itens["itens"][0]["id"]
    for url in (
        f["root"] + "/execucoes",
        f["eurl"],
        f["eurl"] + "/itens",
        f["eurl"] + "/itens/" + iid + "/tentativas",
    ):
        response = f["c"].get(url)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        for campo in (
            "proprietario_id",
            "sessao_id",
            "token",
            "texto",
            "cifrado",
            "chave",
            "telefone_e164",
            "identidade_contato_id",
        ):
            assert campo not in response.text
    assert b2.linhas(f, "tentativas_envio") == []
    assert f["c"].get(f["eurl"] + "/itens", params={"pagina": 2}).json()["itens"] == []


def test_vinculos_inexistentes_recuperacao_nao_publica(http):
    f = http
    iniciar(f)
    for url in (
        f["eurl"].replace(f["cid"], str(uuid4())),
        f["eurl"].replace(f["eid"], str(uuid4())),
        f["eurl"] + "/itens/" + str(uuid4()) + "/tentativas",
    ):
        assert f["c"].get(url).status_code == 404
    for path in ("/recuperacao", "/recuperar", f["eurl"] + "/recuperacao"):
        assert f["c"].post(path).status_code == 404


@pytest.mark.parametrize("comando", ["inicio", "retomada"])
@pytest.mark.parametrize("mesma_operacao", [True, False])
def test_comandos_concorrentes_uma_ativacao(http, monkeypatch, comando, mesma_operacao):
    f = http
    if comando == "retomada":
        iniciar(f)
    integrar(f)
    body = corpo_inicio(f) if comando == "inicio" else corpo_retomada(f)
    url = f["url"] + "/execucoes" if comando == "inicio" else f["eurl"] + "/retomada"
    barreira, liberar = Barrier(2), Event()
    original = ExecutorMock._reservar
    ativar, chamadas = f["rt"].activate, []

    def reservar(executor, adm):
        assert liberar.wait(10)
        return original(executor, adm)

    def contar(*args, **kwargs):
        chamadas.append(True)
        return ativar(*args, **kwargs)

    monkeypatch.setattr(ExecutorMock, "_reservar", reservar)
    monkeypatch.setattr(f["rt"], "activate", contar)

    def enviar(n):
        data = dict(body)
        if n and not mesma_operacao:
            data["operacao_id"] = str(uuid4())
        barreira.wait(timeout=10)
        return f["c"].post(url, json=data)

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(enviar, range(2)))
        assert len(chamadas) == 1
        if mesma_operacao:
            assert all(r.status_code in (200, 201) for r in results)
            assert sum(r.json()["replay"] for r in results) == 1
        else:
            assert sorted(r.status_code for r in results) == [
                201 if comando == "inicio" else 200,
                409,
            ]
    finally:
        liberar.set()
    c.esperar(f["rt"])
    assert len(b2.linhas(f, "tentativas_envio")) == 1


def test_replay_pausa_antiga_nao_suspende_nova_admissao(http, monkeypatch):
    f = http
    iniciar(f)
    p = {"operacao_id": str(uuid4()), "versao": f["c"].get(f["eurl"]).json()["versao"]}
    assert f["c"].post(f["eurl"] + "/pausa", json=p).status_code == 200
    integrar(f)
    liberar, iniciou = Event(), Event()
    original = ExecutorMock._reservar

    def reservar(executor, adm):
        iniciou.set()
        assert liberar.wait(10)
        return original(executor, adm)

    monkeypatch.setattr(ExecutorMock, "_reservar", reservar)
    try:
        response = f["c"].post(f["eurl"] + "/retomada", json=corpo_retomada(f))
        assert response.status_code == 200 and iniciou.wait(10)
        replay = f["c"].post(f["eurl"] + "/pausa", json=p)
        assert replay.status_code == 200 and replay.json()["replay"]
        assert replay.json()["atual"]["operacional"]["admitida"]
    finally:
        liberar.set()
    c.esperar(f["rt"])


@pytest.mark.parametrize(
    "evento", ["logout", "expiracao", "revogacao", "optout", "shutdown", "worker"]
)
def test_mudanca_entre_persistencia_e_ativacao_nao_despacha(http, monkeypatch, evento):
    f = http
    lifetime = integrar(f)
    original = f["rt"].activate

    def mudar(*args, **kwargs):
        if evento == "logout":
            f["app"].state.auth.logout(f["token"])
        elif evento == "expiracao":
            auth = f["app"].state.auth
            stamp = auth.clock() + timedelta(days=2)
            monkeypatch.setattr(auth, "clock", lambda: stamp)
        elif evento == "revogacao":
            b1.revogar(f)
        elif evento == "optout":
            # Mesmo comando de bloqueio usado nas regressoes B1/B2.
            b1.anterior.bloquear(f, "bloqueio_global")
        elif evento == "worker":
            raise NegocioError("worker_indisponivel", "Detalhe privado", 503)
        else:
            lifetime.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(f["rt"], "activate", mudar)
    response = f["c"].post(f["url"] + "/execucoes", json=corpo_inicio(f))
    assert response.status_code == (401 if evento in {"logout", "expiracao"} else 201), (
        response.text
    )
    assert len(b2.linhas(f, "campanha_execucoes")) == 1
    assert b2.linhas(f, "campanha_execucoes")[0]["estado"] != "executando"
    assert b2.linhas(f, "tentativas_envio") == []


@pytest.mark.parametrize(
    "alteracao", ["marcador_retomada", "geracao", "versao", "preparacao_id", "confirmo_retomada"]
)
def test_retomada_adulterada(http, alteracao):
    f = http
    iniciar(f)
    integrar(f)
    data = corpo_retomada(f)
    if alteracao == "marcador_retomada":
        data[alteracao] = "ativar-v1." + "0" * 64
    elif alteracao == "confirmo_retomada":
        data[alteracao] = 1
    elif alteracao == "preparacao_id":
        data[alteracao] = str(uuid4())
    else:
        data[alteracao] += 1
    response = f["c"].post(f["eurl"] + "/retomada", json=data)
    assert response.status_code in (409, 422)
    assert b2.linhas(f, "tentativas_envio") == []


def test_sessao_revalidada_apos_espera_pelo_lock(http, monkeypatch):
    f = http
    body, aguardando = corpo_inicio(f), Event()
    http_service = f["app"].state.execucoes_http
    original = http_service._comando

    @contextmanager
    def comando():
        aguardando.set()
        with original():
            yield

    monkeypatch.setattr(http_service, "_comando", comando)
    with ThreadPoolExecutor(1) as pool:
        with f["svc"].repo.lock:
            future = pool.submit(f["c"].post, f["url"] + "/execucoes", json=body)
            assert aguardando.wait(10)
            auth = f["app"].state.auth
            stamp = auth.clock() + timedelta(days=2)
            monkeypatch.setattr(auth, "clock", lambda: stamp)
        assert future.result(timeout=10).status_code == 401
    assert b2.linhas(f, "campanha_execucoes") == []


def test_banco_erro_sanitizado_e_rollback(http):
    f = http
    body = corpo_inicio(f)
    segredo = "+5511999999999-conteudo-privado"

    def falhar(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO campanha_execucoes"):
            raise OperationalError(statement, params, RuntimeError(segredo))

    event.listen(f["db"].engine, "before_cursor_execute", falhar)
    try:
        response = f["c"].post(f["url"] + "/execucoes", json=body)
        assert response.status_code == 503 and segredo not in response.text
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", falhar)
    assert f["c"].get(f["root"] + "/execucoes").json()["total"] == 0


def test_pausa_tem_espera_limitada_e_polling_nao_usa_lock(http):
    f = http
    iniciar(f)
    body = {"operacao_id": str(uuid4()), "versao": f["c"].get(f["eurl"]).json()["versao"]}
    with ThreadPoolExecutor(1) as pool:
        with f["svc"].repo.lock:
            result = pool.submit(f["c"].get, f["eurl"] + "/itens").result(timeout=2)
            assert result.status_code == 200
            start = perf_counter()
            result = pool.submit(f["c"].post, f["eurl"] + "/pausa", json=body).result(timeout=4)
            assert result.status_code == 503 and result.json()["codigo"] == "operacao_ocupada"
            assert perf_counter() - start < 4


def test_retomada_nao_repete_desconhecido(simulavel, monkeypatch):
    volume.popular(simulavel, 3)
    f, receipt, ctx, executor, adm = b2.construir(simulavel, "desconhecida")
    executor.passo(adm)
    primeira = b2.linhas(f, "tentativas_envio")[0]
    assert primeira["estado"] == "desconhecida"
    ctx.fechar()
    f["rt"] = f["app"].state.execution_runtime
    f["rt"]._recover()
    integrar(f)
    f["eid"], f["eurl"] = receipt["execucao_id"], f["root"] + "/execucoes/" + receipt["execucao_id"]
    response = f["c"].post(f["eurl"] + "/retomada", json=corpo_retomada(f))
    assert response.status_code == 200, response.text
    c.esperar(f["rt"])
    attempts = b2.linhas(f, "tentativas_envio")
    assert len(attempts) == 3
    assert sum(t["item_id"] == primeira["item_id"] for t in attempts) == 1
    assert f["c"].get(f["eurl"]).json()["estado"] == "concluida_com_falhas"


def test_paginacao_8000_custo_constante_por_pagina_sem_dpapi(simulavel, monkeypatch):
    volume.popular(simulavel, 8000)
    f, receipt, ctx, _, _ = b2.construir(simulavel)
    ctx.fechar()
    assert f["app"].state.telegram_recepcao.parar()
    url = f["root"] + "/execucoes/" + receipt["execucao_id"] + "/itens"
    queries = []

    def contar(conn, cursor, statement, params, context, many):
        if statement.lstrip().startswith("SELECT"):
            queries.append(statement)

    def proibido(*args, **kwargs):
        pytest.fail("Polling abriu fotografia")

    monkeypatch.setattr(preparacoes_mod, "abrir", proibido)
    monkeypatch.setattr(f["app"].state.preparacoes, "_autenticar_fotografia", proibido)
    event.listen(f["db"].engine, "before_cursor_execute", contar)
    try:
        start = perf_counter()
        small = f["c"].get(url, params={"limite": 1})
        assert small.status_code == 200
        custo = len(queries)
        ids, ordens = [], []
        for pagina in range(1, 161):
            queries.clear()
            response = f["c"].get(url, params={"pagina": pagina, "limite": 50})
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 8000 and len(data["itens"]) == 50
            assert len(queries) == custo and custo <= 16
            ids.extend(item["id"] for item in data["itens"])
            ordens.extend(item["ordem"] for item in data["itens"])
        elapsed = perf_counter() - start
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", contar)
    assert len(set(ids)) == 8000 and ordens == list(range(1, 8001))
    assert f["c"].get(url, params={"pagina": 161, "limite": 50}).json()["itens"] == []
    with f["db"].engine.connect() as conn:
        plan = conn.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT id FROM campanha_execucao_itens "
            "WHERE execucao_id=? ORDER BY ordem,id LIMIT 50",
            (receipt["execucao_id"],),
        ).all()
        assert any("INDEX" in row[3] for row in plan)
    print(f"\nD1 8000 itens: 160 paginas, {custo} SELECTs/pagina, {elapsed:.3f}s HTTP total")


def test_retomada_commit_falha_reverte_geracao_e_recibo(http):
    f = http
    iniciar(f)
    integrar(f)
    body = corpo_retomada(f)
    antes = f["c"].get(f["eurl"]).json()
    pendente = []

    def detectar(conn, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO auditoria") and body["operacao_id"] in params:
            pendente.append(True)

    def falhar(conn):
        if pendente:
            pendente.clear()
            raise OperationalError("COMMIT", {}, RuntimeError("detalhe privado"))

    engine = f["db"].engine
    event.listen(engine, "before_cursor_execute", detectar)
    event.listen(engine, "commit", falhar)
    try:
        response = f["c"].post(f["eurl"] + "/retomada", json=body)
        assert response.status_code == 503, response.text
        assert "detalhe privado" not in response.text
    finally:
        event.remove(engine, "before_cursor_execute", detectar)
        event.remove(engine, "commit", falhar)
    depois = f["c"].get(f["eurl"]).json()
    assert (depois["versao"], depois["geracao"]) == (antes["versao"], antes["geracao"])
    assert not any(a["id"] == body["operacao_id"] for a in b2.linhas(f, "auditoria"))
    assert b2.linhas(f, "tentativas_envio") == []


def test_marcador_retomada_nao_transfere_sessao_e_replay_novo_login(http):
    f = http
    inicio = corpo_inicio(f)
    receipt = iniciar(f, inicio)
    integrar(f)
    old = corpo_retomada(f)
    response = f["c"].post(
        "/auth/login", json={"login": "teste", "senha": "frase fictícia de teste 489!"}
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    f["c"].headers["x-edisk-session"] = token
    f["token"] = token
    assert f["c"].post(f["eurl"] + "/retomada", json=old).status_code == 409
    replay = iniciar(f, inicio)
    assert replay["replay"] and replay["recibo"] == receipt["recibo"]
    assert b2.linhas(f, "tentativas_envio") == []
    new = corpo_retomada(f)
    response = f["c"].post(f["eurl"] + "/retomada", json=new)
    assert response.status_code == 200, response.text
    c.esperar(f["rt"])
    divergent = f["c"].post(f["eurl"] + "/retomada", json=new | {"geracao": new["geracao"] + 1})
    assert divergent.status_code == 409


@pytest.mark.parametrize("falha", ["dpapi", "recuperacao", "worker", "shutdown"])
def test_consulta_segura_processamento_indisponivel(http, monkeypatch, falha):
    f = http
    iniciar(f)
    lifetime = integrar(f)
    if falha == "dpapi":

        def indisponivel(*a, **kw):
            raise NegocioError("fotografia_indisponivel", "Fotografia indisponivel.", 503)

        monkeypatch.setattr(preparacoes_mod, "abrir", indisponivel)
    elif falha == "recuperacao":
        f["rt"].recovery_ok = False
    elif falha == "worker":
        f["rt"].close_admission()
        f["rt"]._thread.join(timeout=5)
    else:
        lifetime.close()
    assert f["c"].get(f["eurl"]).status_code == 200
    assert f["c"].get(f["eurl"] + "/itens").status_code == 200
    response = f["c"].get(f["eurl"] + "/revisao-retomada")
    assert response.status_code == (503 if falha == "dpapi" else 409)
    assert b2.linhas(f, "tentativas_envio") == []


def test_execucoes_paginadas_ordenadas_contagens_sem_n_mais_um(http, monkeypatch):
    f = http
    ids = []
    for n in range(3):
        result = iniciar(f)
        ids.append(result["recibo"]["execucao_id"])
        if n < 2:
            b1.retornar(f)
            nova = b1.anterior.confirmar(f["simulavel"])
            f.update(nova)
            autorizacao, _ = b1.anterior.autorizar(f)
            f["autorizacao"] = autorizacao
            f["url"] = f["root"] + "/preparacoes/" + f["pid"]
    queries = []

    def contar(conn, cursor, statement, params, context, many):
        if statement.startswith("SELECT"):
            queries.append(statement)

    event.listen(f["db"].engine, "before_cursor_execute", contar)
    try:
        url = f["root"] + "/execucoes"
        pequeno = f["c"].get(url, params={"limite": 1})
        custo = len(queries)
        queries.clear()
        todos = f["c"].get(url, params={"limite": 50})
        assert len(queries) == custo
        assert pequeno.status_code == todos.status_code == 200
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", contar)
    data = todos.json()
    assert data["total"] == 3 and [e["id"] for e in data["itens"]] == ids
    assert [e["contagens"]["interrompido"] for e in data["itens"]] == [1, 1, 0]
    page1 = f["c"].get(url, params={"limite": 2}).json()["itens"]
    page2 = f["c"].get(url, params={"limite": 2, "pagina": 2}).json()["itens"]
    assert [e["id"] for e in page1 + page2] == ids


def test_tentativas_paginadas_ordenadas_reserva_interrompida_permite_nova(simulavel):
    f, receipt, ctx, executor, adm = b2.construir(simulavel)
    tid = executor._reservar(adm)
    ctx.fechar()
    f["rt"] = f["app"].state.execution_runtime
    f["rt"]._recover()
    integrar(f)
    f["eid"], f["eurl"] = receipt["execucao_id"], f["root"] + "/execucoes/" + receipt["execucao_id"]
    response = f["c"].post(f["eurl"] + "/retomada", json=corpo_retomada(f))
    assert response.status_code == 200, response.text
    c.esperar(f["rt"])
    iid = b2.linhas(f, "tentativas_envio")[0]["item_id"]
    url = f["eurl"] + "/itens/" + iid + "/tentativas"
    p1 = f["c"].get(url, params={"limite": 1}).json()
    p2 = f["c"].get(url, params={"limite": 1, "pagina": 2}).json()
    assert p1["total"] == p2["total"] == 2
    assert p1["itens"][0]["id"] == tid and p1["itens"][0]["numero"] == 1
    assert p1["itens"][0]["estado"] == "interrompida"
    assert p2["itens"][0]["numero"] == 2 and p2["itens"][0]["estado"] == "aceita_provedor"
    assert f["c"].get(url, params={"limite": 51}).status_code == 422
