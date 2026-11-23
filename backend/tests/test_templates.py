"""Regras e contratos reais do catálogo; dados descartáveis e sem provedores."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
import test_contatos
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from app.contatos.models import Auditoria
from app.templates.models import Template, TemplateVersao
from app.templates.schemas import VersaoEntrada

cadastro = test_contatos.cadastro


def entrada(**changes):
    return {
        "operacao_id": str(uuid4()),
        "nome": "Aviso de promoção",
        "categoria": "promocoes",
        "plataforma": "whatsapp",
        "conteudo": "Olá, {{nome}}! https://example.invalid/oferta",
        **changes,
    }


def criar(c, **changes):
    result = c.post("/templates", json=entrada(**changes))
    assert result.status_code == 201, result.text
    return result.json()


def test_catalogo_historico_filtros_edicao_arquivo_e_replay(cadastro):
    c, app, _ = cadastro
    body = entrada()
    first = c.post("/templates", json=body).json()
    root = "/templates/" + first["id"]
    assert c.post("/templates", json=body).json() == first
    assert c.post("/templates", json={**body, "nome": "Divergente"}).status_code == 409
    for platform, number in [("whatsapp", 2), ("telegram", 1), ("telegram", 2)]:
        request = {
            "operacao_id": str(uuid4()),
            "plataforma": platform,
            "conteudo": f"Conteúdo {platform} {number}",
        }
        result = c.post(root + "/versoes", json=request)
        assert result.status_code == 201, result.text
        assert c.post(root + "/versoes", json=request).json() == result.json()
    old = c.get(root + "/versoes", params={"plataforma": "whatsapp"}).json()
    assert [v["numero_versao"] for v in old["itens"]] == [2, 1]
    assert old["itens"][1]["conteudo"] == body["conteudo"]
    assert old["itens"][1]["id"] == first["template_versao_id"]
    for query in [
        {"q": "PROMOCAO"},
        {"categoria": "promocoes", "plataforma": "telegram"},
        {"limite": 1, "pagina": 1},
    ]:
        assert c.get("/templates", params=query).json()["total"] == 1
    assert c.get("/templates", params={"q": "%"}).json()["total"] == 0
    assert c.get("/templates", params={"categoria": "eventos_grupos"}).json()["total"] == 0
    assert c.get(root + "/versoes", params={"limite": 2, "pagina": 2}).json()["total"] == 4
    assert c.get(root + "/versoes", params={"limite": 2, "pagina": 3}).json()["itens"] == []
    metadata = c.get(root).json()
    edit = {
        "operacao_id": str(uuid4()),
        "versao": metadata["versao"],
        "nome": "Novo nome",
        "descricao": "Descrição privada",
    }
    response = c.patch(root, json=edit)
    assert response.status_code == 200
    assert c.patch(root, json={**edit, "operacao_id": str(uuid4())}).status_code == 409
    archived = c.patch(
        root,
        json={
            "operacao_id": str(uuid4()),
            "versao": response.json()["versao"],
            "ativo": False,
        },
    )
    assert archived.status_code == 200
    # Repetição retorna o recibo original, inclusive após novas alterações.
    assert c.patch(root, json=edit).json() == response.json()
    assert c.post("/templates", json=body).json() == first
    assert c.get("/templates").json()["total"] == 0
    assert c.get("/templates", params={"ativo": False}).json()["total"] == 1
    assert c.get(root + "/versoes").json()["total"] == 4
    assert (
        c.post(
            root + "/versoes",
            json={
                "operacao_id": str(uuid4()),
                "plataforma": "whatsapp",
                "conteudo": "Texto",
            },
        ).status_code
        == 409
    )
    assert (
        c.patch(
            root,
            json={
                "operacao_id": str(uuid4()),
                "versao": archived.json()["versao"],
                "categoria": "eventos_grupos",
            },
        ).status_code
        == 422
    )
    with app.state.contatos.repo.transacao() as s:
        audit = " ".join(s.scalars(select(Auditoria.resumo_json)))
        assert body["conteudo"] not in audit
        assert "Descrição privada" not in audit
        assert "Novo nome" not in audit
        assert s.scalar(select(func.count()).select_from(TemplateVersao)) == 4


@pytest.mark.parametrize(
    "content",
    [
        "{{email}}",
        "{{ nome }}",
        "{{nome",
        "nome}}",
        "{nome}",
        "{{{{nome}}}}",
        "{{__import__('os')}}",
        "\x00",
        " ",
        "a" * 4001,
    ],
)
def test_parser_rejeita_sintaxe_sem_vazar_entrada(cadastro, content):
    c, _, _ = cadastro
    assert c.post("/templates", json=entrada(conteudo=content)).status_code == 422
    result = c.post(
        "/templates/previa",
        json={
            "plataforma": "whatsapp",
            "conteudo": content,
            "valores": {"nome": "Fictício"},
        },
    )
    assert result.status_code == 422
    assert "__import__" not in result.text
    assert c.get("/templates").json()["total"] == 0


def test_previa_literal_unicode_limites_sem_persistencia(cadastro, caplog):
    c, app, _ = cadastro
    body = {
        "plataforma": "telegram",
        "conteudo": "Olá {{nome}}\n{{nome}} 🔔",
        "valores": {"nome": "{{email}}"},
    }
    result = c.post("/templates/previa", json=body)
    assert result.json()["conteudo_renderizado"] == "Olá {{email}}\n{{email}} 🔔"
    assert result.json()["validacao"] == "somente_conteudo_local"
    assert c.post("/templates/previa", json={**body, "valores": {}}).status_code == 422
    assert (
        c.post(
            "/templates/previa",
            json={
                **body,
                "valores": {"email": "segredo"},
            },
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/templates/previa",
            json={
                **body,
                "valores": {"nome": "x" * 121},
            },
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/templates/previa",
            json={
                **body,
                "conteudo": "a" * 4000,
                "valores": {},
            },
        ).status_code
        == 200
    )
    assert "{{email}}" not in caplog.text
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Template)) == 0
        assert s.scalar(select(func.count()).select_from(Auditoria)) == 0


def test_constraints_imutabilidade_e_rollback(cadastro, monkeypatch):
    c, app, token = cadastro
    item = criar(c)
    db = app.state.persistence.database
    for sql in [
        "UPDATE template_versoes SET conteudo='Alterado'",
        "DELETE FROM template_versoes",
        "INSERT OR REPLACE INTO template_versoes SELECT * FROM template_versoes",
        "INSERT OR REPLACE INTO templates SELECT * FROM templates",
        "UPDATE templates SET categoria='eventos_grupos'",
        "DELETE FROM templates",
        "UPDATE templates SET versao=0",
        "INSERT INTO template_versoes SELECT * FROM template_versoes",
    ]:
        with pytest.raises(IntegrityError), db.transaction() as s:
            s.execute(text(sql))
    service = app.state.templates

    def fail(*_):
        raise RuntimeError("Falha antes do commit")

    monkeypatch.setattr(service, "_registrar", fail)
    with pytest.raises(RuntimeError):
        service.versionar(
            item["id"],
            VersaoEntrada(
                operacao_id=uuid4(),
                plataforma="telegram",
                conteudo="Não deve persistir",
            ),
            token,
        )
    assert c.get("/templates/" + item["id"]).json()["versao"] == 1
    assert c.get("/templates/" + item["id"] + "/versoes").json()["total"] == 1


def test_numero_concorrente_e_repeticao_simultanea(cadastro):
    c, app, token = cadastro
    item = criar(c)
    svc = app.state.templates
    requests = [
        VersaoEntrada(
            operacao_id=uuid4(),
            plataforma="whatsapp",
            conteudo=f"Versão {i}",
        )
        for i in range(8)
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda d: svc.versionar(item["id"], d, token), requests))
        replay = list(
            executor.map(
                lambda _: svc.versionar(item["id"], requests[0], token),
                range(4),
            )
        )
    assert all(r == results[0] for r in replay)
    history = svc.historico(item["id"])["itens"]
    assert [v["numero_versao"] for v in history] == list(range(9, 0, -1))
    assert len({v["id"] for v in history}) == 9


@pytest.mark.parametrize("mutation", ["criar", "versionar", "editar"])
@pytest.mark.parametrize("invalidation", ["logout", "expiracao"])
def test_revalida_sessao_apos_lock(cadastro, monkeypatch, mutation, invalidation):
    c, app, _ = cadastro
    item = criar(c)
    root = "/templates/" + item["id"]
    method, path, body = {
        "criar": (c.post, "/templates", entrada()),
        "versionar": (
            c.post,
            root + "/versoes",
            {
                "operacao_id": str(uuid4()),
                "plataforma": "telegram",
                "conteudo": "Outro",
            },
        ),
        "editar": (
            c.patch,
            root,
            {
                "operacao_id": str(uuid4()),
                "versao": 1,
                "nome": "Outro nome",
            },
        ),
    }[mutation]
    repo = app.state.contatos.repo
    lock, waiting = repo.lock, Event()

    class ObservedLock:
        def __enter__(self):
            waiting.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    monkeypatch.setattr(repo, "lock", ObservedLock())
    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(method, path, json=body)
            assert waiting.wait(10)
            if invalidation == "logout":
                assert c.post("/auth/logout").status_code == 204
            else:
                now = datetime.now(UTC) + timedelta(hours=9)
                monkeypatch.setattr(app.state.auth, "clock", lambda: now)
        assert future.result(10).status_code == 401
    with repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Template)) == 1
        assert s.scalar(select(func.count()).select_from(TemplateVersao)) == 1
        assert s.scalar(select(func.count()).select_from(Auditoria)) == 1


def test_fronteira_http_credenciais_erros_e_limites(cadastro):
    c, app, _ = cadastro
    assert c.post("/templates", content=b"x" * 32769).status_code == 413
    assert c.get("/templates", params={"limite": 51}).status_code == 422
    assert c.get("/templates", params={"plataforma": "mock"}).status_code == 422
    assert c.get("/templates/" + str(uuid4())).status_code == 404
    assert c.get("/templates/not-a-uuid").status_code == 422
    original = dict(c.headers)
    del c.headers["X-eDisk-Session"]
    assert c.post("/templates", content="not json").status_code == 401
    c.headers.update(original)
    del c.headers["Authorization"]
    assert c.get("/templates").status_code == 401
    c.headers.update(original)
    # O perfil operador já existe: as mesmas operações autenticadas são reutilizadas.
    with app.state.persistence.database.transaction() as s:
        s.execute(text("UPDATE usuarios SET perfil='operador'"))
    assert c.post("/templates", json=entrada()).status_code == 201
    with app.state.persistence.database.transaction() as s:
        audit = json.loads(s.scalar(select(Auditoria.resumo_json)))
        assert set(audit) == {"requisicao_hash", "resultado"}


def test_replay_edicao_rejeita_apenas_versao_esperada_diferente(cadastro):
    c, app, _ = cadastro
    item = criar(c)
    root = "/templates/" + item["id"]
    request = {
        "operacao_id": str(uuid4()),
        "versao": 1,
        "nome": "Nome revisado",
    }
    result = c.patch(root, json=request)
    assert result.status_code == 200, result.text
    assert c.patch(root, json=request).json() == result.json()
    divergent = c.patch(root, json={**request, "versao": 2})
    assert divergent.status_code == 409
    assert divergent.json()["codigo"] == "conflito"
    assert c.patch(root, json=request).json() == result.json()
    assert c.get(root).json()["versao"] == 2
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Auditoria)) == 2
        assert s.scalar(select(func.count()).select_from(TemplateVersao)) == 1


def test_rollback_auditoria_apos_flush_preserva_e_permite_repetir(cadastro, monkeypatch):
    c, app, token = cadastro
    item = criar(c)
    service = app.state.templates
    identifier = item["id"]
    service.versionar(
        identifier,
        VersaoEntrada(
            operacao_id=uuid4(),
            plataforma="whatsapp",
            conteudo="Segunda versão",
        ),
        token,
    )
    request = VersaoEntrada(
        operacao_id=uuid4(),
        plataforma="whatsapp",
        conteudo="Terceira versão",
    )

    def snapshot():
        with app.state.contatos.repo.transacao() as s:
            return {
                model.__tablename__: s.execute(
                    select(model.__table__).order_by(*model.__table__.primary_key.columns)
                ).all()
                for model in (Template, TemplateVersao, Auditoria)
            }

    before = snapshot()
    observed = []
    original = service._registrar

    def registrar_apos_flush(s, data, usuario, digest, acao, result):
        s.flush()
        # Consulta SQL: confirma a versão efetivamente gravada, sem recorrer ao identity map.
        row = s.execute(
            select(TemplateVersao.__table__).where(
                TemplateVersao.id == result["template_versao_id"],
            )
        ).one()
        assert row.numero_versao == 3
        observed.append(row.id)
        return original(s, data, usuario, digest, acao, result)

    def falhar_auditoria(_mapper, connection, target):
        if target.id == str(request.operacao_id):
            # O INSERT da auditoria também aconteceu; toda a transação precisa reverter.
            assert (
                connection.execute(
                    select(Auditoria.id).where(
                        Auditoria.id == target.id,
                    )
                ).scalar_one()
                == target.id
            )
            assert (
                connection.execute(
                    select(TemplateVersao.id).where(
                        TemplateVersao.id == observed[0],
                    )
                ).scalar_one()
                == observed[0]
            )
            raise RuntimeError("Falha determinística na auditoria após flush.")

    event.listen(Auditoria, "after_insert", falhar_auditoria)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, "_registrar", registrar_apos_flush)
            with pytest.raises(RuntimeError, match="após flush"):
                service.versionar(identifier, request, token)
    finally:
        event.remove(Auditoria, "after_insert", falhar_auditoria)

    assert len(observed) == 1
    assert snapshot() == before
    result = service.versionar(identifier, request, token)
    assert service.versionar(identifier, request, token) == result
    history = service.historico(identifier)["itens"]
    assert [v["numero_versao"] for v in history] == [3, 2, 1]
    assert [v["conteudo"] for v in history[1:]] == ["Segunda versão", entrada()["conteudo"]]
    assert all(v["id"] != observed[0] for v in history)
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Auditoria)) == 3
        assert s.scalar(select(func.count()).select_from(TemplateVersao)) == 3
        assert s.get(Auditoria, str(request.operacao_id)) is not None
