"""Contas mock: isolamento de credenciais e fronteira do Marco A."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_contas_envio as contas
import test_preparacoes as prep
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.models import Usuario
from app.campanhas.models import Campanha
from app.contas_envio.models import ContaEnvio
from app.contas_envio.schemas import ContaEdicao, ContaEntrada
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria

cadastro = contas.cadastro
consentimento = prep.consentimento
rascunho = prep.rascunho
pronta = prep.pronta


def entrada(**changes):
    return contas.entrada(plataforma="whatsapp", provedor="mock", **changes)


def proibir_segredos(app, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Fluxo mock acessou credenciais, limpeza global ou rede.")

    for name in ("assinatura", "gravar", "recuperar", "limpar", "pendente"):
        monkeypatch.setattr(app.state.contas_envio.segredos, name, forbidden)
    monkeypatch.setattr(app.state.contas_envio, "recuperar", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_ciclo_mock_com_conta_real_sem_acessar_segredos(cadastro, monkeypatch, caplog):
    c, app, _ = cadastro
    real = contas.criar(c)
    assert (
        c.put(
            "/contas-envio/" + real["id"] + "/credencial",
            json={
                **prep.op(versao=1),
                "credencial": "sentinela-real-protegida",
            },
        ).status_code
        == 200
    )
    proibir_segredos(app, monkeypatch)
    body = entrada()
    created = c.post("/contas-envio", json=body)
    assert created.status_code == 201, created.text
    first = created.json()
    assert c.post("/contas-envio", json=body).json() == first
    assert c.post("/contas-envio", json={**body, "nome": "Divergente"}).status_code == 409
    root = "/contas-envio/" + first["id"]
    detail = c.get(root).json()
    assert detail["configuracao_local"] == "simulacao_sem_credenciais"
    assert detail["modo"] == "simulacao" and detail["conexao"] == "nao_aplicavel"
    assert "segredo_ref" not in detail
    assert c.get("/contas-envio", params={"provedor": "mock"}).json()["total"] == 1
    revision = 1
    for changes in ({"ativo": False}, {"ativo": True}, {"nome": "Mock editada"}, {"remover": True}):
        body = prep.op(versao=revision, **changes)
        result = c.patch(root, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["limpeza_pendente"] is False
        assert c.patch(root, json=body).json() == result.json()
        revision += 1
    assert c.get(root).json()["estado_cadastral"] == "removida"
    assert c.patch(root, json=prep.op(versao=revision, ativo=True)).status_code == 409
    assert (
        c.get("/contas-envio", params={"provedor": "mock", "estado": "removida"}).json()["total"]
        == 1
    )
    with app.state.contatos.repo.transacao() as s:
        assert s.get(ContaEnvio, first["id"]).segredo_ref is None
        audits = s.scalars(select(Auditoria).where(Auditoria.entidade_id == first["id"])).all()
        assert len(audits) == 5
        for audit in audits:
            assert set(json.loads(audit.resumo_json)) == {"requisicao_hash", "resultado"}
    assert "sentinela-real-protegida" not in caplog.text


def test_listagem_apenas_mock_nao_consulta_armazenamento(cadastro, monkeypatch):
    c, app, _ = cadastro
    contas.criar(c, plataforma="whatsapp", provedor="mock")
    proibir_segredos(app, monkeypatch)
    assert c.get("/contas-envio").json()["total"] == 1


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_mock_rejeita_credencial_antes_de_dpapi(cadastro, monkeypatch, method):
    c, app, _ = cadastro
    first = contas.criar(c, plataforma="whatsapp", provedor="mock")
    proibir_segredos(app, monkeypatch)
    body = prep.op(versao=1)
    if method == "PUT":
        body["credencial"] = "sentinela-mock-proibida"
    response = c.request(method, "/contas-envio/" + first["id"] + "/credencial", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["codigo"] == "credencial_nao_aplicavel"
    assert c.get("/contas-envio/" + first["id"]).json()["versao"] == 1
    with app.state.contatos.repo.transacao() as s:
        assert (
            len(s.scalars(select(Auditoria).where(Auditoria.entidade_id == first["id"])).all()) == 1
        )


def test_mock_combinacoes_conversoes_e_constraints(cadastro):
    c, app, _ = cadastro
    first = contas.criar(c, plataforma="whatsapp", provedor="mock")
    real = contas.criar(c, plataforma="whatsapp", provedor="meta_cloud")
    assert c.post("/contas-envio", json=contas.entrada(provedor="mock")).status_code == 422
    assert c.post("/contas-envio", json=entrada(credencial="proibida")).status_code == 422
    for identifier, provider in ((first["id"], "meta_cloud"), (real["id"], "mock")):
        assert (
            c.patch(
                "/contas-envio/" + identifier, json=prep.op(versao=1, provedor=provider)
            ).status_code
            == 422
        )
        with pytest.raises(IntegrityError), app.state.persistence.database.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE contas_envio SET provedor=? WHERE id=?", (provider, identifier)
            )
    for sql in (
        "UPDATE contas_envio SET segredo_ref='aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        "aaaa' WHERE provedor='mock'",
        "DELETE FROM contas_envio WHERE provedor='mock'",
        "INSERT OR REPLACE INTO contas_envio SELECT * FROM contas_envio WHERE provedor='mock'",
        "INSERT INTO contas_envio SELECT '00000000-0000-0000-0000-00000000"
        "0016',nome,nome_busca,'telegram','mock',ativo,removido_em,segredo"
        "_ref,versao,criado_por_usuario_id,criado_em,atualizado_em FROM co"
        "ntas_envio WHERE provedor='mock'",
        "INSERT INTO contas_envio SELECT '00000000-0000-0000-0000-00000000"
        "0016',nome,nome_busca,'whatsapp','mock',ativo,removido_em,'aaaaaa"
        "aaaaaaaaaaaaaaaaaaaaaaaaaa',versao,criado_por_usuario_id,criado_e"
        "m,atualizado_em FROM contas_envio WHERE provedor='mock'",
    ):
        with pytest.raises(IntegrityError), app.state.persistence.database.engine.begin() as conn:
            conn.exec_driver_sql(sql)


def test_mock_exige_administrador_e_sessao(cadastro, monkeypatch):
    c, app, token = cadastro
    first = contas.criar(c, plataforma="whatsapp", provedor="mock")
    proibir_segredos(app, monkeypatch)
    with app.state.persistence.database.transaction() as s:
        s.execute(update(Usuario).values(perfil="operador"))
    assert c.post("/contas-envio", json=entrada()).status_code == 403
    assert (
        c.patch("/contas-envio/" + first["id"], json=prep.op(versao=1, ativo=False)).status_code
        == 403
    )
    with pytest.raises(NegocioError) as error:
        app.state.contas_envio.criar(ContaEntrada(**entrada()), token)
    assert error.value.status == 403
    c.headers["X-eDisk-Session"] = "0" * 64
    assert c.post("/contas-envio", json=entrada()).status_code == 401


def test_mock_revisao_concorrente_e_replay(cadastro, monkeypatch):
    _, app, token = cadastro
    proibir_segredos(app, monkeypatch)
    svc = app.state.contas_envio
    data = ContaEntrada(**entrada())
    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(lambda _: svc.criar(data, token), range(4)))
    assert all(item == created[0] for item in created)
    identifier = created[0]["id"]

    def change(i):
        try:
            return svc.editar(identifier, ContaEdicao(**prep.op(versao=1, nome=f"Mock {i}")), token)
        except NegocioError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(change, range(4)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 3
    with app.state.contatos.repo.transacao() as s:
        assert s.get(ContaEnvio, identifier).versao == 2
        assert (
            len(s.scalars(select(Auditoria).where(Auditoria.entidade_id == identifier)).all()) == 2
        )


@pytest.mark.parametrize("edit", [False, True])
def test_mock_rollback_de_auditoria(cadastro, edit):
    c, app, _ = cadastro
    first = contas.criar(c, plataforma="whatsapp", provedor="mock") if edit else None
    db = app.state.persistence.database
    body = prep.op(versao=1, ativo=False) if edit else entrada()

    def fail(conn, cursor, sql, *_):
        if sql.startswith("INSERT INTO auditoria"):
            assert conn.exec_driver_sql("SELECT count(*) FROM contas_envio").scalar_one() == 1
            if edit:
                assert conn.exec_driver_sql("SELECT ativo FROM contas_envio").scalar_one() == 0
            raise SQLAlchemyError("Falha injetada apos escrita")

    event.listen(db.engine, "after_cursor_execute", fail)
    try:
        response = (
            c.patch("/contas-envio/" + first["id"], json=body)
            if edit
            else c.post("/contas-envio", json=body)
        )
        assert response.status_code == 503, response.text
    finally:
        event.remove(db.engine, "after_cursor_execute", fail)
    with db.engine.begin() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM auditoria WHERE id=?", (body["operacao_id"],)
            ).scalar_one()
            == 0
        )
        if edit:
            assert conn.exec_driver_sql("SELECT ativo,versao FROM contas_envio").one() == (1, 1)
        else:
            assert conn.exec_driver_sql("SELECT count(*) FROM contas_envio").scalar_one() == 0


def test_marco_a_nao_prepara_mock_sem_configuracao(pronta):
    r, identifier, _ = pronta
    c, app, *_ = r
    account = contas.criar(c, plataforma="whatsapp", provedor="mock")
    review = c.get(f"/campanhas/{identifier}/revisao-preparacao").json()
    assert (
        c.patch(
            f"/campanhas/{identifier}",
            json=prep.op(versao=review["versao"], conta_envio_id=account["id"]),
        ).status_code
        == 200
    )
    assert (
        c.get(f"/campanhas/{identifier}/revisao-preparacao").json()["codigo"]
        == "configuracao_obrigatoria"
    )
    receipt = c.get(f"/campanhas/{identifier}").json()
    service = app.state.preparacoes
    with service.repo.transacao() as session:
        marker = service._marcador(
            session, session.get(Campanha, identifier), service._ctx(session, True)
        )
    response = c.post(
        f"/campanhas/{identifier}/preparacoes",
        json=prep.op(versao=receipt["versao"], marcador=marker),
    )
    assert response.status_code == 422
    with app.state.persistence.database.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 0
