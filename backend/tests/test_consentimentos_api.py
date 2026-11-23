"""Contratos HTTP, filtros e integração com importação."""

from uuid import uuid4

import pytest
import test_consentimentos
import test_contatos
from sqlalchemy import text
from test_consentimentos import (
    avaliar,
    entrada,
    registrar,
)
from test_contatos import criar

cadastro = test_contatos.cadastro
consentimento = test_consentimentos.consentimento


def test_api_estado_eventos_e_repeticao(consentimento):
    c, _, _, contact, scope, identity, _ = consentimento
    root = f"/contatos/{contact['id']}"
    query = {"escopo_id": scope["id"]}
    assert c.get(root + "/identidades").json()["itens"][0]["id"] == identity["id"]
    assert c.get(root + "/consentimentos", params=query).json()["versao"] == 1
    body = entrada(scope, identity, 1).model_dump(mode="json")
    response = c.post(root + "/consentimentos/eventos", json=body)
    assert response.status_code == 201, response.text
    assert c.post(root + "/consentimentos/eventos", json=body).json() == response.json()
    assert response.json()["usuario_id"]
    eligibility = c.get(
        root + "/elegibilidade-consentimento",
        params={
            **query,
            "identidade_id": identity["id"],
            "categoria": "promocoes",
        },
    ).json()
    assert eligibility["elegivel_consentimento"]
    history = c.get(root + "/consentimentos/eventos", params={**query, "limite": 1}).json()
    assert history["total"] == 1
    assert (
        c.get(root + "/consentimentos/eventos", params={**query, "limite": 51}).status_code == 422
    )
    assert c.patch(root + "/consentimentos/eventos", json={}).status_code == 405


def test_criacao_explicita_escopo_e_identidade_sem_autorizar(cadastro):
    c, _, _ = cadastro
    assert c.get("/contatos/escopos-comunicacao").json() == {"itens": []}
    scope_body = {"operacao_id": str(uuid4()), "nome": "Operação autorizada"}
    scope = c.post("/contatos/escopos-comunicacao", json=scope_body)
    assert scope.status_code == 201
    assert c.post("/contatos/escopos-comunicacao", json=scope_body).json() == scope.json()
    contact = criar(c).json()
    root = f"/contatos/{contact['id']}"
    body = {
        "operacao_id": str(uuid4()),
        "telefone_id": contact["telefones"][0]["id"],
        "versao": 0,
        "canal": "whatsapp",
    }
    identity = c.post(root + "/identidades", json=body)
    assert identity.status_code == 201, identity.text
    assert c.post(root + "/identidades", json=body).json() == identity.json()
    assert c.post(root + "/identidades", json={**body, "versao": 1}).status_code == 409
    assert c.post(root + "/identidades", json={**body, "canal": "telegram"}).status_code == 422
    state = c.get(root + "/consentimentos", params={"escopo_id": scope.json()["id"]}).json()
    assert all(p["estado"] == "desconhecido" for p in state["identidades"][0]["preferencias"])


def test_filtros_efetivos_sem_duplicar_contato(consentimento):
    f = consentimento
    c, _, _, _, scope, _, _ = f
    criar(c, telefone="+442083661177")
    query = {"escopo_id": scope["id"], "categoria": "promocoes"}
    assert (
        c.get("/contatos", params={**query, "consentimento": "desconhecido"}).json()["total"] == 2
    )
    registrar(f)
    assert c.get("/contatos", params={**query, "consentimento": "autorizado"}).json()["total"] == 1
    registrar(f, "bloqueio_global", None, identity=False)
    assert c.get("/contatos", params={**query, "consentimento": "autorizado"}).json()["total"] == 0
    assert c.get("/contatos", params={**query, "consentimento": "bloqueado"}).json()["total"] == 1
    assert c.get("/contatos", params={"consentimento": "autorizado"}).status_code == 422
    assert c.get("/contatos", params={"canal": "whatsapp"}).json()["total"] == 1


def test_api_validacao_sanitizada_e_duas_credenciais(consentimento):
    c, _, _, contact, scope, identity, _ = consentimento
    root = f"/contatos/{contact['id']}"
    body = entrada(scope, identity, 1).model_dump(mode="json")
    private = "EVIDENCIA-PRIVADA-NAO-EXIBIR"
    invalid = {**body, "evidencia_referencia": private * 100}
    response = c.post(root + "/consentimentos/eventos", json=invalid)
    assert response.status_code == 422 and private not in response.text
    for credential in ("X-eDisk-Session", "Authorization"):
        value = c.headers.pop(credential)
        try:
            assert c.post(root + "/consentimentos/eventos", json=body).status_code == 401
            assert c.get("/contatos/escopos-comunicacao").status_code == 401
        finally:
            c.headers[credential] = value


def test_identidade_de_outro_contato_e_fks(consentimento):
    c, app, _, contact, scope, identity, _ = consentimento
    other = criar(c, telefone="+442083661177").json()
    root = f"/contatos/{other['id']}"
    body = entrada(scope, identity, 0).model_dump(mode="json")
    assert c.post(root + "/consentimentos/eventos", json=body).status_code == 404
    from app.contatos.errors import NegocioError

    with pytest.raises(NegocioError) as error:
        with app.state.contatos.repo.transacao() as s:
            s.execute(
                text(
                    "INSERT INTO identidades_contato "
                    "(id,contato_id,telefone_id,canal,criado_por_usuario_id,criado_em) "
                    "SELECT :id,:c,:p,'whatsapp',id,'2026-01-01T00:00:00.000000Z' FROM usuarios"
                ),
                {"id": str(uuid4()), "c": other["id"], "p": contact["telefones"][0]["id"]},
            )
    assert error.value.status == 409


def test_reimportacao_e_previa_preservam_restricoes(consentimento):
    f = consentimento
    c, app, svc, contact, scope, _, _ = f
    from test_importacoes import preparar

    registrar(f)
    assert avaliar(f)["elegivel_consentimento"]
    config = {
        "coluna_nome": 0,
        "coluna_telefone": 1,
        "politica": "substituir",
        "campos_atualizar": ["nome"],
    }
    identifier, _ = preparar(
        (c, app, None),
        b"nome;telefone\nAtualizado;+5511912345678",
        config=config,
    )
    registrar(f, "revogou")
    before = svc.historico(contact["id"], scope["id"])
    assert [event["tipo"] for event in before["itens"]] == ["revogou", "autorizou"]
    response = c.post(f"/importacoes/{identifier}/confirmacao")
    assert response.status_code == 409 and response.json()["codigo"] == "previa_desatualizada"
    response = c.post(f"/importacoes/{identifier}/validacao", json=config)
    assert response.status_code == 202
    app.state.importacoes.future.result(timeout=30)
    response = c.post(f"/importacoes/{identifier}/confirmacao")
    assert response.status_code == 200
    assert response.json()["contagens"]["atualizado"] == 1
    updated = c.get(f"/contatos/{contact['id']}").json()
    assert updated["nome"] == "Atualizado" and updated["versao"] == contact["versao"] + 1
    assert svc.historico(contact["id"], scope["id"]) == before
    result = avaliar(f)
    assert result["estado"] == "bloqueado" and not result["elegivel_consentimento"]
    assert result["motivos"] == ["categoria_revogada"]
    with app.state.contatos.repo.transacao() as session:
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_arquivamento_e_substituicao_nao_transferem(consentimento):
    f = consentimento
    c, _, svc, contact, scope, identity, token = f
    registrar(f)
    old = contact["telefones"][0]
    assert (
        c.patch(
            f"/contatos/{contact['id']}/telefones/{old['id']}",
            json={"versao": 1, "ativo": False},
        ).status_code
        == 200
    )
    assert not avaliar(f)["elegivel_consentimento"]
    assert "telefone_arquivado" in avaliar(f)["motivos"]
    from uuid import UUID

    from app.consentimentos.schemas import IdentidadeEntrada

    phone = c.post(
        f"/contatos/{contact['id']}/telefones", json={"telefone": "+442083661177"}
    ).json()
    other = svc.criar_identidade(
        contact["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(phone["id"]),
            canal="whatsapp",
            versao=2,
        ),
        token,
    )
    assert (
        svc.elegibilidade(
            contact["id"],
            scope["id"],
            other["id"],
            "promocoes",
        )["estado"]
        == "desconhecido"
    )
    assert (
        c.patch(f"/contatos/{contact['id']}", json={"versao": 1, "ativo": False}).status_code == 200
    )
    assert "contato_arquivado" in avaliar(f)["motivos"]


def test_nao_reutiliza_evidencia_anterior_a_revogacao(consentimento):
    f = consentimento
    _, _, svc, contact, scope, identity, token = f
    first = entrada(scope, identity, 1)
    svc.registrar(contact["id"], first, token)
    registrar(f, "revogou")
    stale = entrada(scope, identity, 3, ocorrido_em=first.ocorrido_em)
    from app.contatos.errors import NegocioError

    with pytest.raises(NegocioError) as ex:
        svc.registrar(contact["id"], stale, token)
    assert ex.value.codigo == "evidencia_anterior"


@pytest.mark.parametrize("mutacao", ["escopo", "identidade", "evento"])
@pytest.mark.parametrize("invalidacao", ["logout", "expiracao"])
def test_sessao_encerrada_enquanto_mutacao_aguarda_lock(
    cadastro, monkeypatch, mutacao, invalidacao
):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta
    from threading import Event

    from sqlalchemy import select

    from app.consentimentos.models import (
        EscopoComunicacao,
        EventoConsentimento,
        IdentidadeContato,
        PreferenciaContato,
    )
    from app.contatos.models import Auditoria, Contato

    c, app, _ = cadastro
    contact = criar(c).json()
    scope_body = {"operacao_id": str(uuid4()), "nome": "Operação local de teste"}
    root = f"/contatos/{contact['id']}"
    identity_body = {
        "operacao_id": str(uuid4()),
        "telefone_id": contact["telefones"][0]["id"],
        "canal": "whatsapp",
        "versao": 0,
    }
    if mutacao == "escopo":
        path, body = "/contatos/escopos-comunicacao", scope_body
    elif mutacao == "identidade":
        path, body = root + "/identidades", identity_body
    else:
        scope = c.post("/contatos/escopos-comunicacao", json=scope_body).json()
        identity = c.post(root + "/identidades", json=identity_body).json()
        path = root + "/consentimentos/eventos"
        body = entrada(scope, identity, 1).model_dump(mode="json")

    repo = app.state.contatos.repo
    models = (
        EventoConsentimento,
        PreferenciaContato,
        Auditoria,
        IdentidadeContato,
        EscopoComunicacao,
        Contato,
    )

    def snapshot():
        with repo.transacao() as session:
            return {
                model.__tablename__: session.execute(
                    select(model.__table__).order_by(*model.__table__.primary_key.columns)
                ).all()
                for model in models
            }

    before = snapshot()
    waiting = Event()
    original_lock = repo.lock

    class ObservedLock:
        def __enter__(self):
            waiting.set()
            original_lock.acquire()
            return self

        def __exit__(self, *_):
            original_lock.release()

    monkeypatch.setattr(repo, "lock", ObservedLock())
    with ThreadPoolExecutor(max_workers=1) as executor:
        with original_lock:
            future = executor.submit(c.post, path, json=body)
            assert waiting.wait(timeout=10), "A mutação não alcançou o lock."
            if invalidacao == "logout":
                assert c.post("/auth/logout").status_code == 204
            else:
                expired_now = datetime.now(UTC) + timedelta(hours=9)
                monkeypatch.setattr(app.state.auth, "clock", lambda: expired_now)
        response = future.result(timeout=10)

    assert response.status_code == 401, response.text
    assert response.json()["codigo"] == "sessao_invalida"
    assert snapshot() == before


def test_replay_identidade_preserva_resultado_original_e_nao_desbloqueia(consentimento):
    from uuid import UUID

    from app.consentimentos.schemas import IdentidadeEntrada
    from app.contatos.errors import NegocioError

    c, _, svc, contact, scope, identity, token = consentimento
    request = IdentidadeEntrada(
        operacao_id=UUID(identity["id"]),
        telefone_id=UUID(identity["telefone_id"]),
        canal="whatsapp",
        versao=0,
    )
    registrar(consentimento, "bloqueio_canal", None)
    assert svc.criar_identidade(contact["id"], request, token) == identity
    with pytest.raises(NegocioError) as ex:
        svc.criar_identidade(
            contact["id"],
            request.model_copy(update={"versao": 1}),
            token,
        )
    assert ex.value.status == 409
    state = svc.estado(contact["id"], scope["id"])
    assert state["versao"] == 2
    assert state["identidades"][0]["bloqueio_canal_em"] is not None
    assert len(c.get(f"/contatos/{contact['id']}/identidades").json()["itens"]) == 1
    assert all(p["estado"] == "bloqueado" for p in state["identidades"][0]["preferencias"])
