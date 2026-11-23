"""Servico/API reais de autorizacao, sem processamento nem efeitos externos."""

import hashlib
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Event, get_ident
from uuid import UUID, uuid4

import pytest
import test_consentimentos as consent
import test_preparacoes_correcao as correcao
import test_preparacoes_simulacao as sim
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.campanhas import preparacoes as preparacoes_mod
from app.campanhas.autorizacoes import AutorizacoesSimulacaoService
from app.campanhas.configuracao_simulacao import resumo_configuracao
from app.campanhas.models import Preparacao
from app.campanhas.schemas import AutorizarSimulacaoEntrada, TransicaoEntrada
from app.consentimentos.schemas import IdentidadeEntrada
from app.contas_envio.schemas import ContaEdicao
from app.contatos.models import Auditoria
from app.provedores.mock import MockProvedor

cadastro = sim.cadastro
consentimento = sim.consentimento
rascunho = sim.rascunho
pronta = sim.pronta
simulavel = sim.simulavel


def confirmar(simulavel):
    receipt, _ = sim.preparar(simulavel)
    sim.confirmar(simulavel, receipt)
    r, identifier, _ = simulavel
    return {
        "c": r[0],
        "app": r[1],
        "r": r,
        "simulavel": simulavel,
        "root": "/campanhas/" + identifier,
        "pid": receipt["preparacao_id"],
    }


@pytest.fixture
def confirmada(simulavel):
    return confirmar(simulavel)


def revisar(f):
    response = f["c"].get(f["root"] + "/preparacoes/" + f["pid"] + "/revisao-autorizacao")
    assert response.status_code == 200, response.text
    return response.json()


def entrada(f, review=None, **changes):
    review = revisar(f) if review is None else review
    return {
        "operacao_id": str(uuid4()),
        "versao": review["versao"],
        "impressao": review["impressao"],
        "resumo_configuracao": review["resumo_configuracao"],
        "marcador_autorizacao": review["marcador_autorizacao"],
        "confirmo_simulacao": True,
        **changes,
    }


def postar(f, body):
    return f["c"].post(
        f["root"] + "/preparacoes/" + f["pid"] + "/autorizacoes-simulacao", json=body
    )


def autorizar(f, body=None):
    body = entrada(f) if body is None else body
    response = postar(f, body)
    assert response.status_code == 201, response.text
    return response.json(), body


def revogar(f, receipt, body=None):
    body = (
        sim.prep.op(versao=receipt["versao_autorizacao"], motivo_codigo="revogacao_manual")
        if body is None
        else body
    )
    response = f["c"].post(
        f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"] + "/revogacao",
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json(), body


def obter(f, receipt):
    return f["c"].get(f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"])


def snapshot(f):
    with f["app"].state.persistence.database.engine.begin() as conn:
        names = (
            conn.exec_driver_sql(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            .scalars()
            .all()
        )
        return {
            name: sorted(
                [tuple(r) for r in conn.exec_driver_sql('SELECT * FROM "' + name + '"')], key=repr
            )
            for name in names
        }


def bloquear(f, tipo):
    consent.registrar(
        f["r"][-1],
        tipo,
        "promocoes" if tipo == "revogou" else None,
        identity=tipo != "bloqueio_global",
    )


def test_revisao_nova_autenticada_sem_efeitos_e_ciclo_idempotente(confirmada):
    f = confirmada
    before = snapshot(f)
    review = revisar(f)
    assert review == revisar(f)
    assert review["autorizavel"] is True
    assert review["avaliacao_atual"]["integridade"] == "verificada"
    assert review["avaliacao_atual"]["elegiveis_atuais"] == 1
    assert review["processamento_disponivel"] is False
    assert snapshot(f) == before
    body = entrada(f, review)
    receipt, _ = autorizar(f, body)
    assert receipt["recibo_historico"] and receipt["validade_atual"] == "nao_avaliada"
    assert receipt["estado_decisao"] == "vigente"
    assert receipt["versao_autorizacao"] == 1
    assert receipt["versao"] == review["versao"] + 1
    assert postar(f, body).json() == receipt
    assert postar(f, {**body, "versao": body["versao"] + 1}).status_code == 409
    assert postar(f, {**body, "operacao_id": str(uuid4())}).status_code == 409
    read = obter(f, receipt)
    assert read.status_code == 200
    assert read.json()["validade_atual"]["condicoes_atuais_atendidas"] is True
    assert read.json()["processamento_disponivel"] is False
    assert read.headers["cache-control"] == "no-store"
    assert len(snapshot(f)["campanha_autorizacoes_simulacao"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"confirmo_simulacao": False},
        {"confirmo_simulacao": 1},
        {"confirmo_simulacao": "true"},
        {"confirmo_simulacao": None},
        {"versao": 0},
        {"versao": True},
        {"impressao": "X" * 64},
        {"resumo_configuracao": "g" * 64},
        {"marcador_autorizacao": "marcador-antigo"},
        {"texto": "nao-aceitar"},
    ],
)
def test_entradas_estritas_e_erro_sanitizado(confirmada, change):
    body = entrada(confirmada, **change)
    before = snapshot(confirmada)
    response = postar(confirmada, body)
    assert response.status_code == 422, response.text
    assert "nao-aceitar" not in response.text and snapshot(confirmada) == before


def test_confirmacao_obrigatoria_e_marcador_da_abertura(confirmada):
    f = confirmada
    body = entrada(f)
    del body["confirmo_simulacao"]
    assert postar(f, body).status_code == 422
    body = entrada(f)
    f["app"].state.autorizacoes_simulacao = AutorizacoesSimulacaoService(f["app"].state.preparacoes)
    assert postar(f, body).status_code == 409
    assert revisar(f)["marcador_autorizacao"] != body["marcador_autorizacao"]


def test_nao_confirmada_e_preparacao_de_outra_campanha(simulavel):
    receipt, _ = sim.preparar(simulavel)
    c, app, *_ = simulavel[0]
    root = "/campanhas/" + simulavel[1]
    path = root + "/preparacoes/" + receipt["preparacao_id"] + "/revisao-autorizacao"
    assert c.get(path).status_code == 409
    sim.confirmar(simulavel, receipt)
    other = sim.prep.campanhas.criar(simulavel[0], operacao_id=str(uuid4()))
    assert c.get(path.replace(simulavel[1], other["id"], 1)).status_code == 404


def test_conta_real_rejeitada_sem_autorizacao(pronta):
    receipt, _ = sim.prep.preparar(pronta)
    c = pronta[0][0]
    root = "/campanhas/" + pronta[1] + "/preparacoes/" + receipt["preparacao_id"]
    detail = c.get(root).json()
    assert (
        c.post(
            root + "/confirmacao",
            json=sim.prep.op(
                versao=receipt["versao"],
                preparacao_id=receipt["preparacao_id"],
                impressao=detail["impressao"],
            ),
        ).status_code
        == 200
    )
    assert c.get(root + "/revisao-autorizacao").status_code == 409
    body = sim.prep.op(
        versao=receipt["versao"] + 1,
        impressao=detail["impressao"],
        resumo_configuracao="a" * 64,
        marcador_autorizacao="autorizacao-v1." + "a" * 64,
        confirmo_simulacao=True,
    )
    assert c.post(root + "/autorizacoes-simulacao", json=body).status_code == 409


@pytest.mark.parametrize(
    "tipo,motivo",
    [
        ("revogou", "categoria_revogada"),
        ("bloqueio_global", "bloqueio_global"),
        ("bloqueio_canal", "bloqueio_canal"),
    ],
)
def test_optout_posterior_nao_reescreve_fotografia_nem_autoriza_zero(confirmada, tipo, motivo):
    f = confirmada
    body = entrada(f)
    photos = snapshot(f)["campanha_preparacoes"]
    bloquear(f, tipo)
    assert postar(f, body).status_code == 409
    review = revisar(f)
    assert review["autorizavel"] is False
    atual = review["avaliacao_atual"]
    assert atual["elegiveis_atuais"] == 0 and atual["impedidos_atuais"] == 1
    assert motivo in atual["impedimentos"]
    assert postar(f, entrada(f, review)).status_code == 409
    assert snapshot(f)["campanha_preparacoes"] == photos
    assert snapshot(f)["campanha_autorizacoes_simulacao"] == []


@pytest.mark.parametrize(
    "mutation", ["conta", "consentimento", "preparacao", "auditoria", "versao"]
)
def test_mudanca_entre_revisao_e_autorizacao(confirmada, mutation):
    f = confirmada
    review, app = revisar(f), f["app"]
    body = entrada(f, review)
    if mutation == "conta":
        cfg = sim.fotografia(f["simulavel"], {"preparacao_id": f["pid"]})["referencias_congeladas"][
            "configuracao_simulacao"
        ]
        app.state.contas_envio.editar(
            cfg["conta_envio_id"], ContaEdicao(**sim.prep.op(versao=1, ativo=False)), f["r"][-2]
        )
        assert revisar(f)["autorizavel"] is False
    elif mutation == "consentimento":
        bloquear(f, "revogou")
    elif mutation == "preparacao":
        assert (
            f["c"]
            .post(
                f["root"] + "/retorno-rascunho",
                json=sim.prep.op(versao=review["versao"], preparacao_id=f["pid"]),
            )
            .status_code
            == 200
        )
    elif mutation == "auditoria":
        assert f["c"].post("/listas", json={"nome": "Lista sem relacao"}).status_code == 201
    else:
        body["versao"] += 1
    assert postar(f, body).status_code == 409
    assert snapshot(f)["campanha_autorizacoes_simulacao"] == []


def test_revogacao_terminal_replay_historico_e_fotografia_intacta(confirmada):
    f = confirmada
    receipt, body = autorizar(f)
    photos = snapshot(f)["campanha_preparacoes"]
    revoked, revoke_body = revogar(f, receipt)
    assert revoked["versao_autorizacao"] == 2
    assert revoked["estado_decisao"] == "revogada"
    assert revogar(f, receipt, revoke_body)[0] == revoked
    assert postar(f, body).json() == receipt  # Recibo original, nunca reativa a decisao.
    path = f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"] + "/revogacao"
    assert f["c"].post(path, json={**revoke_body, "operacao_id": str(uuid4())}).status_code == 409
    assert f["c"].post(path, json={**revoke_body, "versao": 2}).status_code == 409
    assert snapshot(f)["campanha_preparacoes"] == photos
    read = obter(f, receipt).json()
    assert read["estado"] == "revogada"
    assert read["validade_atual"]["condicoes_atuais_atendidas"] is False
    assert "autorizacao_revogada" in read["validade_atual"]["impedimentos"]


@pytest.mark.parametrize("tipo", ["revogou", "bloqueio_global", "bloqueio_canal"])
def test_estado_vigente_nao_comprova_validade_atual(confirmada, tipo):
    f = confirmada
    receipt, _ = autorizar(f)
    before = obter(f, receipt).json()
    bloquear(f, tipo)
    snap = snapshot(f)
    after = obter(f, receipt).json()
    assert before["estado"] == after["estado"] == "vigente"
    assert before["validade_atual"]["condicoes_atuais_atendidas"] is True
    assert after["validade_atual"]["condicoes_atuais_atendidas"] is False
    assert after["processamento_disponivel"] is False
    assert snapshot(f) == snap


def test_retorno_rascunho_revoga_atomico_e_replay_nao_reativa(confirmada):
    f = confirmada
    receipt, body = autorizar(f)
    before = snapshot(f)
    ret = sim.prep.op(versao=receipt["versao"], preparacao_id=f["pid"])
    response = f["c"].post(f["root"] + "/retorno-rascunho", json=ret)
    assert response.status_code == 200, response.text
    assert f["c"].post(f["root"] + "/retorno-rascunho", json=ret).json() == response.json()
    assert postar(f, body).json() == receipt
    read = obter(f, receipt).json()
    assert read["estado"] == "revogada" and read["motivo_codigo"] == "retorno_rascunho"
    assert f["c"].get(f["root"]).json()["status"] == "rascunho"
    after = snapshot(f)
    assert len(after["auditoria"]) == len(before["auditoria"]) + 2
    assert after["campanha_preparacao_itens"] == before["campanha_preparacao_itens"]


def test_historico_paginado_deterministico_e_sem_chave_para_vazio(confirmada):
    f = confirmada
    assert f["c"].get(f["root"] + "/autorizacoes-simulacao").json()["itens"] == []
    receipts = []
    for n in range(3):
        receipt, _ = autorizar(f)
        receipts.append(receipt)
        if n < 2:
            ret = f["c"].post(
                f["root"] + "/retorno-rascunho",
                json=sim.prep.op(versao=receipt["versao"], preparacao_id=f["pid"]),
            )
            assert ret.status_code == 200
            prepared, _ = sim.preparar(f["simulavel"])
            sim.confirmar(f["simulavel"], prepared)
            f["pid"] = prepared["preparacao_id"]
    snap = snapshot(f)
    for page, receipt in enumerate(reversed(receipts), 1):
        response = f["c"].get(
            f["root"] + "/autorizacoes-simulacao", params={"pagina": page, "limite": 1}
        )
        assert response.status_code == 200
        assert response.json()["total"] == 3
        assert response.json()["itens"][0]["id"] == receipt["autorizacao_id"]
    assert (
        f["c"].get(f["root"] + "/autorizacoes-simulacao", params={"limite": 51}).status_code == 422
    )
    assert snapshot(f) == snap
    other = sim.prep.campanhas.criar(f["r"], operacao_id=str(uuid4()))
    assert (
        f["c"]
        .get(
            "/campanhas/" + other["id"] + "/autorizacoes-simulacao/" + receipts[0]["autorizacao_id"]
        )
        .status_code
        == 404
    )


@pytest.mark.parametrize("same", [False, True])
def test_autorizacoes_concorrentes_serializadas(confirmada, monkeypatch, same):
    f = confirmada
    body = entrada(f)
    other = body if same else {**body, "operacao_id": str(uuid4())}
    svc = f["app"].state.autorizacoes_simulacao
    barrier, original = Barrier(2), svc.autorizar

    def synchronized(*args):
        barrier.wait(timeout=15)
        return original(*args)

    monkeypatch.setattr(svc, "autorizar", synchronized)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda b: postar(f, b), [body, other]))
    assert sorted(r.status_code for r in responses) == ([201, 201] if same else [201, 409])
    if same:
        assert responses[0].json() == responses[1].json()
    assert len(snapshot(f)["campanha_autorizacoes_simulacao"]) == 1


@pytest.mark.parametrize("mutation", ["logout", "expiracao", "consentimento", "conta", "retorno"])
def test_autorizar_revalida_apos_espera_pelo_lock(confirmada, monkeypatch, mutation):
    f, waiting = confirmada, Event()
    app, r = f["app"], f["r"]
    account = f["c"].get(f["root"]).json()["conta_envio_id"]
    data = AutorizarSimulacaoEntrada(**entrada(f))
    repo = app.state.autorizacoes_simulacao.repo
    lock, main = repo.lock, get_ident()

    class Observed:
        def __enter__(self):
            if get_ident() != main:
                waiting.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    monkeypatch.setattr(repo, "lock", Observed())
    with ThreadPoolExecutor(1) as pool:
        with lock:
            pending = pool.submit(
                app.state.autorizacoes_simulacao.autorizar,
                f["root"].split("/")[-1],
                f["pid"],
                data,
                r[-2],
            )
            assert waiting.wait(10)
            if mutation == "logout":
                app.state.auth.logout(r[-2])
            elif mutation == "expiracao":
                clock = app.state.auth.clock
                monkeypatch.setattr(app.state.auth, "clock", lambda: clock() + timedelta(hours=9))
            elif mutation == "consentimento":
                bloquear(f, "revogou")
            elif mutation == "conta":
                app.state.contas_envio.editar(
                    account, ContaEdicao(**sim.prep.op(versao=1, ativo=False)), r[-2]
                )
            else:
                app.state.preparacoes.transicao(
                    f["root"].split("/")[-1],
                    TransicaoEntrada(**sim.prep.op(versao=data.versao, preparacao_id=f["pid"])),
                    r[-2],
                )
        with pytest.raises(Exception) as exc:
            pending.result(timeout=15)
    assert exc.value.status == (401 if mutation in {"logout", "expiracao"} else 409)
    assert snapshot(f)["campanha_autorizacoes_simulacao"] == []


@pytest.mark.parametrize("same", [False, True])
def test_revogacoes_concorrentes_e_replay_de_autorizacao(confirmada, monkeypatch, same):
    f = confirmada
    receipt, authorize_body = autorizar(f)
    body = sim.prep.op(versao=1, motivo_codigo="revogacao_manual")
    other = body if same else {**body, "operacao_id": str(uuid4())}
    svc = f["app"].state.autorizacoes_simulacao
    barrier, original = Barrier(2), svc.revogar

    def synchronized(*args):
        barrier.wait(timeout=15)
        return original(*args)

    monkeypatch.setattr(svc, "revogar", synchronized)
    path = f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"] + "/revogacao"
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda b: f["c"].post(path, json=b), [body, other]))
    assert sorted(r.status_code for r in responses) == ([200, 200] if same else [200, 409])
    if same:
        assert responses[0].json() == responses[1].json()
    assert postar(f, authorize_body).json() == receipt
    assert obter(f, receipt).json()["estado"] == "revogada"


def test_autorizacao_concorrente_com_retorno_nao_deixa_vigente_invalidada(confirmada):
    f, barrier = confirmada, Barrier(2)
    body = entrada(f)
    ret = sim.prep.op(versao=body["versao"], preparacao_id=f["pid"])

    def run(authorize):
        barrier.wait(timeout=15)
        return (
            postar(f, body) if authorize else f["c"].post(f["root"] + "/retorno-rascunho", json=ret)
        )

    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(run, [True, False]))
    assert (a.status_code, b.status_code) in {(201, 409), (409, 200)}
    with f["app"].state.persistence.database.engine.begin() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_autorizacoes_simulacao a "
                "JOIN campanha_preparacoes p ON p.id=a.preparacao_id "
                "WHERE a.estado='vigente' AND p.invalidado_em IS NOT NULL"
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("action", ["autorizar", "revogar", "retornar"])
@pytest.mark.parametrize("failure", ["auditoria", "commit"])
def test_rollback_apos_escrita_comprovada(confirmada, action, failure):
    f, seen = confirmada, []
    app = f["app"]
    if action == "autorizar":
        body, path = entrada(f), f["root"] + "/preparacoes/" + f["pid"] + "/autorizacoes-simulacao"
    else:
        receipt, _ = autorizar(f)
        if action == "revogar":
            body = sim.prep.op(versao=1, motivo_codigo="revogacao_manual")
            path = f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"] + "/revogacao"
        else:
            body = sim.prep.op(versao=receipt["versao"], preparacao_id=f["pid"])
            path = f["root"] + "/retorno-rascunho"
    before = snapshot(f)

    def check(conn):
        state = conn.exec_driver_sql(
            "SELECT estado FROM campanha_autorizacoes_simulacao"
        ).scalar_one()
        assert state == ("vigente" if action == "autorizar" else "revogada")
        if action == "retornar":
            assert (
                conn.exec_driver_sql(
                    "SELECT invalidado_em FROM campanha_preparacoes WHERE id=?", (f["pid"],)
                ).scalar_one()
                is not None
            )
        seen.append(True)
        raise OperationalError("falha-sintetica", {}, RuntimeError("conteudo-privado-nao-vazar"))

    def after_insert(mapper, conn, target):
        if target.id == body["operacao_id"]:
            check(conn)

    def commit(conn):
        if conn.exec_driver_sql(
            "SELECT 1 FROM auditoria WHERE id=?", (body["operacao_id"],)
        ).first():
            check(conn)

    target, name, listener = (
        (Auditoria, "after_insert", after_insert)
        if failure == "auditoria"
        else (app.state.persistence.database.engine, "commit", commit)
    )
    event.listen(target, name, listener)
    try:
        response = f["c"].post(path, json=body)
    finally:
        event.remove(target, name, listener)
    assert seen
    assert response.status_code == 503, response.text
    assert "conteudo-privado" not in response.text
    assert snapshot(f) == before
    assert f["c"].post(path, json=body).status_code == (201 if action == "autorizar" else 200)


def test_expiracao_antes_do_commit_reverte_decisao(confirmada, monkeypatch):
    f, app = confirmada, confirmada["app"]
    body, before = entrada(f), snapshot(f)
    original, clock = app.state.autorizacoes_simulacao._recibo, app.state.auth.clock

    def expire(*args):
        result = original(*args)
        monkeypatch.setattr(app.state.auth, "clock", lambda: clock() + timedelta(hours=9))
        return result

    monkeypatch.setattr(app.state.autorizacoes_simulacao, "_recibo", expire)
    assert postar(f, body).status_code == 401
    assert snapshot(f) == before


def test_perfis_existentes_e_403_para_perfil_incompativel(confirmada, monkeypatch):
    f, app = confirmada, confirmada["app"]
    with app.state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE usuarios SET perfil='operador'")
    body = entrada(f)
    original = app.state.auth.current

    def incompatible(*args, **kwargs):
        session = original(*args, **kwargs)
        return session.model_copy(
            update={"usuario": session.usuario.model_copy(update={"perfil": "incompativel"})}
        )

    monkeypatch.setattr(app.state.auth, "current", incompatible)
    assert postar(f, body).status_code == 403
    assert (
        f["c"].get(f["root"] + "/preparacoes/" + f["pid"] + "/revisao-autorizacao").status_code
        == 403
    )
    monkeypatch.setattr(app.state.auth, "current", original)
    receipt, _ = autorizar(f, body)  # Operador autorizado pelo contrato atual.
    del f["c"].headers["X-eDisk-Session"]
    assert obter(f, receipt).status_code == 401
    assert postar(f, body).status_code == 401


@pytest.mark.parametrize("change", ["referencias", "manifesto", "remocao"])
def test_adulteracao_bloqueia_revisao_e_post_e_historico_e_seguro(confirmada, change):
    f = confirmada
    body = entrada(f)
    receipt, _ = autorizar(f, body)
    correcao.adulterar(f["app"], f["pid"], change, f["r"][4]["id"])
    path = f["root"] + "/preparacoes/" + f["pid"] + "/revisao-autorizacao"
    response = f["c"].get(path)
    assert response.status_code == 503, response.text
    assert response.json()["codigo"] == "integridade_comprometida"
    assert postar(f, {**body, "operacao_id": str(uuid4())}).status_code == 503
    view = obter(f, receipt)
    assert view.status_code == 200
    assert view.json()["validade_atual"]["avaliada"] is False
    assert view.json()["validade_atual"]["integridade"] == "comprometida"
    assert view.json()["processamento_disponivel"] is False
    revogar(f, receipt)  # Revogar nao depende de descriptografar a fotografia danificada.


def test_chave_indisponivel_nao_apresenta_validade_comprovada(confirmada):
    f = confirmada
    receipt, _ = autorizar(f)
    with f["app"].state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE campanha_contexto_criptografico SET chave_dpapi=?", (b"x",))
    read = obter(f, receipt).json()
    assert read["validade_atual"]["avaliada"] is False
    assert read["validade_atual"]["integridade"] == "nao_verificada"
    assert read["estado"] == "vigente"
    revogar(f, receipt)


def test_sem_provedores_rede_credenciais_texto_ou_pii(confirmada, monkeypatch, caplog):
    f, app = confirmada, confirmada["app"]
    private = (
        f["c"]
        .get(f["root"] + "/preparacoes/" + f["pid"] + "/itens/" + f["r"][4]["id"] + "/conteudo")
        .json()["conteudo_renderizado"]
    )

    def forbidden(*_, **__):
        pytest.fail("Operacao externa, credencial ou texto aberto na autorizacao")

    monkeypatch.setattr(MockProvedor, "__init__", forbidden)
    monkeypatch.setattr(MockProvedor, "executar", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in ("assinatura", "gravar", "recuperar", "limpar", "pendente"):
        monkeypatch.setattr(app.state.contas_envio.segredos, name, forbidden)
    original = preparacoes_mod.GCM.decrypt

    def checked(self, nonce, cipher, aad):
        context = json.loads(aad)
        if isinstance(context, dict) and context.get("finalidade") == "texto":
            forbidden()
        return original(self, nonce, cipher, aad)

    monkeypatch.setattr(preparacoes_mod.GCM, "decrypt", checked)
    receipt, _ = autorizar(f)
    responses = [obter(f, receipt)]
    responses.append(f["c"].get(f["root"] + "/autorizacoes-simulacao"))
    revogar(f, receipt)
    with app.state.persistence.database.engine.begin() as conn:
        audit = "\n".join(
            conn.exec_driver_sql(
                "SELECT resumo_json FROM auditoria WHERE acao IN "
                "('autorizar_simulacao','revogar_simulacao')"
            ).scalars()
        )
        tables = set(
            conn.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type='table'").scalars()
        )
        assert not {"mensagens", "fila"} & tables
        for tabela in (
            "campanha_execucoes",
            "campanha_destinatarios",
            "campanha_execucao_itens",
            "tentativas_envio",
            "campanha_execucao_transicoes",
        ):
            assert conn.exec_driver_sql("SELECT count(*) FROM " + tabela).scalar_one() == 0
    output = audit + caplog.text + "".join(r.text for r in responses)
    for value in (private, f["r"][3]["nome"], f["simulavel"][2], "+5511912345678"):
        assert value not in output


@pytest.mark.parametrize("incluida", [False, True])
def test_excluidas_nao_promovidas_e_incluidas_reavaliadas_sem_bloquear_todas(simulavel, incluida):
    r, identifier, _ = simulavel
    c, app, _, contact, _, _, token, consent_fixture = r
    phone = c.post(
        "/contatos/" + contact["id"] + "/telefones", json={"telefone": "+442083661177"}
    ).json()
    new = app.state.consentimentos.criar_identidade(
        contact["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(phone["id"]),
            canal="whatsapp",
            versao=app.state.consentimentos.estado(contact["id"], consent_fixture[4]["id"])[
                "versao"
            ],
        ),
        token,
    )
    second_consent = (*consent_fixture[:5], new, token)
    if incluida:
        consent.registrar(second_consent)
    selected = sim.prep.campanhas.selecionar(c, c.get("/campanhas/" + identifier).json(), new)
    assert selected.status_code == 200, selected.text
    f = confirmar(simulavel)
    if incluida:
        bloquear(f, "revogou")  # So a identidade original perde autorizacao.
    else:
        consent.registrar(second_consent)  # Excluida na fotografia continua excluida.
    review = revisar(f)
    assert review["autorizavel"] is True
    assert review["avaliacao_atual"]["incluidos"] == (2 if incluida else 1)
    assert review["avaliacao_atual"]["excluidos"] == (0 if incluida else 1)
    assert review["avaliacao_atual"]["elegiveis_atuais"] == 1
    autorizar(f, entrada(f, review))


@pytest.mark.parametrize("field", ["impressao", "resumo_configuracao", "marcador_autorizacao"])
def test_hash_do_cliente_nao_substitui_fotografia_e_marcador(confirmada, field):
    f = confirmada
    body = entrada(f)
    body[field] = ("autorizacao-v1." if field == "marcador_autorizacao" else "") + "0" * 64
    before = snapshot(f)
    assert postar(f, body).status_code == 409
    assert snapshot(f) == before


def adulterar_semantica_com_aead_valido(f, change):
    """Somente banco descartavel: manifesto valido nao torna semantica aceitavel."""
    service = f["app"].state.preparacoes
    with service.repo.transacao() as session:
        p = session.get(Preparacao, f["pid"])
        old_header = json.loads(p.fotografia_json)
        master = preparacoes_mod.abrir(service._ctx(session))
        with preparacoes_mod.GCM(master) as root:
            old_key = root.decrypt(
                p.chave_nonce, p.chave_cifrada, preparacoes_mod.aad(old_header, "chave")
            )
        rows = [
            dict(row)
            for row in session.connection()
            .exec_driver_sql(
                "SELECT * FROM campanha_preparacao_itens WHERE preparacao_id=? "
                "ORDER BY identidade_contato_id",
                (f["pid"],),
            )
            .mappings()
        ]
    header = deepcopy(old_header)
    cfg = header["configuracao_simulacao"]
    if change == "versao":
        cfg["versao_configuracao"] = 2
    elif change == "capacidades":
        cfg["capacidades"]["entrega"] = True
    elif change == "conta":
        cfg["conta_envio_id"] = str(uuid4())
    else:
        cfg["modo"] = "real"
    cfg["resumo_configuracao"] = resumo_configuracao(
        {key: value for key, value in cfg.items() if key != "resumo_configuracao"}
    )
    header["marcador"] = (
        ".".join(header["marcador"].split(".")[:4]) + "." + cfg["resumo_configuracao"]
    )
    new_key = os.urandom(32)
    digest = hashlib.sha256(preparacoes_mod.canon(header))
    with preparacoes_mod.GCM(old_key) as old, preparacoes_mod.GCM(new_key) as new:
        for row in rows:
            if row["incluido"]:
                plain = old.decrypt(
                    row["nonce"],
                    row["conteudo_cifrado"],
                    preparacoes_mod.aad(old_header, "texto", row["identidade_contato_id"]),
                )
                row["nonce"] = os.urandom(12)
                row["conteudo_cifrado"] = new.encrypt(
                    row["nonce"],
                    plain,
                    preparacoes_mod.aad(header, "texto", row["identidade_contato_id"]),
                )
            service._hash_item(digest, row)
        manifest_nonce = os.urandom(12)
        manifest = new.encrypt(
            manifest_nonce, digest.digest(), preparacoes_mod.aad(header, "manifesto")
        )
    key_nonce = os.urandom(12)
    with preparacoes_mod.GCM(master) as root:
        wrapped = root.encrypt(key_nonce, new_key, preparacoes_mod.aad(header, "chave"))
    with f["app"].state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
        conn.exec_driver_sql("DROP TRIGGER campanha_preparacao_itens_sem_update")
        for row in rows:
            conn.exec_driver_sql(
                "UPDATE campanha_preparacao_itens SET nonce=?,conteudo_cifrado=? "
                "WHERE preparacao_id=? AND identidade_contato_id=?",
                (row["nonce"], row["conteudo_cifrado"], f["pid"], row["identidade_contato_id"]),
            )
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET fotografia_json=?,chave_nonce=?,chave_cifrada=?,"
            "manifesto_nonce=?,manifesto=?,impressao=? WHERE id=?",
            (
                preparacoes_mod.canon(header).decode("ascii"),
                key_nonce,
                wrapped,
                manifest_nonce,
                manifest,
                hashlib.sha256(manifest_nonce + manifest).hexdigest(),
                f["pid"],
            ),
        )
    with service.repo.transacao() as session, preparacoes_mod.GCM(new_key) as cipher:
        service._integridade(session, session.get(Preparacao, f["pid"]), header, cipher)


@pytest.mark.parametrize("change", ["versao", "capacidades", "conta", "modo"])
def test_semantica_invalida_recusada_mesmo_com_manifesto_valido(confirmada, change):
    f = confirmada
    body = entrada(f)
    adulterar_semantica_com_aead_valido(f, change)
    review = f["c"].get(f["root"] + "/preparacoes/" + f["pid"] + "/revisao-autorizacao")
    assert review.status_code == 503, review.text
    assert review.json()["codigo"] == "integridade_comprometida"
    assert postar(f, body).status_code == 503
    assert snapshot(f)["campanha_autorizacoes_simulacao"] == []


def test_replay_concorrente_com_revogacao_preserva_recibo_e_decisao(confirmada):
    f, barrier = confirmada, Barrier(2)
    receipt, body = autorizar(f)
    revoke_body = sim.prep.op(versao=1, motivo_codigo="revogacao_manual")
    path = f["root"] + "/autorizacoes-simulacao/" + receipt["autorizacao_id"] + "/revogacao"

    def run(replay):
        barrier.wait(timeout=15)
        return postar(f, body) if replay else f["c"].post(path, json=revoke_body)

    with ThreadPoolExecutor(2) as pool:
        replay, revoked = list(pool.map(run, [True, False]))
    assert replay.status_code == 201 and replay.json() == receipt
    assert revoked.status_code == 200
    assert obter(f, receipt).json()["estado"] == "revogada"


def test_historico_integro_apos_edicao_legitima_do_rascunho(confirmada):
    f = confirmada
    receipt, _ = autorizar(f)
    returned = f["c"].post(
        f["root"] + "/retorno-rascunho",
        json=sim.prep.op(versao=receipt["versao"], preparacao_id=f["pid"]),
    )
    assert returned.status_code == 200, returned.text
    other = sim.contas.criar(f["c"], plataforma="whatsapp", provedor="mock")
    changed = f["c"].patch(
        f["root"],
        json=sim.prep.op(versao=returned.json()["versao"], conta_envio_id=other["id"]),
    )
    assert changed.status_code == 200, changed.text
    read = obter(f, receipt)
    assert read.status_code == 200
    atual = read.json()["validade_atual"]
    assert atual["integridade"] == "verificada"
    assert atual["avaliada"] is True and atual["condicoes_atuais_atendidas"] is False
    assert "referencias_campanha_alteradas" in atual["impedimentos"]
    assert read.json()["estado"] == "revogada"
