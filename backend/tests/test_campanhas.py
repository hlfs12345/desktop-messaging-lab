"""Campanhas reais em SQLite temporário: rascunhos, revisão e segurança."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
import test_consentimentos
import test_contas_envio
import test_contatos
import test_templates
from sqlalchemy import event, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.campanhas.models import Campanha, CampanhaSelecao
from app.campanhas.schemas import RascunhoEdicao, RascunhoEntrada
from app.campanhas.service import CampanhasService
from app.consentimentos.models import IdentidadeContato
from app.consentimentos.schemas import IdentidadeEntrada
from app.contatos.models import Auditoria, Contato, ListaContato, TelefoneContato
from app.contatos.repository import agora
from app.persistencia.database import Database

cadastro = test_contatos.cadastro
consentimento = test_consentimentos.consentimento


@pytest.fixture
def rascunho(consentimento):
    c, app, _, contact, scope, identity, token = consentimento
    conta = test_contas_envio.criar(c, plataforma="whatsapp", provedor="qr_candidato")
    template = test_templates.criar(c)
    lista = c.post("/listas", json={"nome": "Lista local"}).json()
    assert c.put("/listas/" + lista["id"] + "/contatos/" + contact["id"]).status_code == 204
    body = {
        "operacao_id": str(uuid4()),
        "nome": "Campanha local",
        "conta_envio_id": conta["id"],
        "escopo_id": scope["id"],
        "template_versao_id": template["template_versao_id"],
        "categoria": "promocoes",
        "listas": [lista["id"]],
    }
    return c, app, body, contact, identity, lista, token, consentimento


def criar(f, **changes):
    c, _, body, *_ = f
    result = c.post("/campanhas", json={**body, **changes})
    assert result.status_code == 201, result.text
    return result.json()


def selecionar(c, item, identity, selecionada=True, **changes):
    return c.put(
        "/campanhas/" + item["id"] + "/selecoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": item["versao"],
            "identidade_contato_id": identity["id"],
            "selecionada": selecionada,
            **changes,
        },
    )


def test_criacao_reabertura_idempotencia_e_auditoria(rascunho):
    c, app, body, _, _, _, token, _ = rascunho
    item = criar(rascunho)
    root = "/campanhas/" + item["id"]
    assert c.post("/campanhas", json=body).json() == item
    assert c.post("/campanhas", json={**body, "nome": "Divergente"}).status_code == 409
    detail = c.get(root).json()
    assert detail["status"] == "rascunho"
    assert detail["listas"] == body["listas"]
    # Nova instância de Database/serviço não depende de estado de memória.
    db = Database(app.state.persistence.settings.database_path)
    try:
        from app.contatos.repository import CadastroRepository

        rebuilt = CampanhasService(
            CadastroRepository(db), app.state.auth, app.state.consentimentos, app.state.templates
        )
        assert rebuilt.obter(item["id"])["nome"] == body["nome"]
    finally:
        db.close()
    edit = {"operacao_id": str(uuid4()), "versao": 1, "nome": "Novo"}
    receipt = c.patch(root, json=edit).json()
    assert receipt["versao"] == 2
    assert c.patch(root, json=edit).json() == receipt
    assert c.patch(root, json={**edit, "versao": 2}).status_code == 409
    with app.state.contatos.repo.transacao() as s:
        audits = s.scalars(select(Auditoria).where(Auditoria.entidade == "campanha")).all()
        assert len(audits) == 2
        for audit in audits:
            summary = json.loads(audit.resumo_json)
            assert set(summary) == {"requisicao_hash", "resultado"}
            assert set(summary["resultado"]) == {"id", "versao"}
            assert body["nome"] not in audit.resumo_json


def test_listas_sobrepostas_identidades_distintas_e_selecao_explicita(rascunho):
    c, app, _, contact, identity, lista, token, f = rascunho
    second = c.post("/listas", json={"nome": "Outra lista"}).json()
    c.put("/listas/" + second["id"] + "/contatos/" + contact["id"])
    phone = c.post(
        "/contatos/" + contact["id"] + "/telefones", json={"telefone": "+442083661177"}
    ).json()
    extra = app.state.consentimentos.criar_identidade(
        contact["id"],
        IdentidadeEntrada(operacao_id=uuid4(), telefone_id=phone["id"], canal="whatsapp", versao=1),
        token,
    )
    item = criar(rascunho, listas=[lista["id"], second["id"]])
    root = "/campanhas/" + item["id"]
    rows = c.get(root + "/revisao").json()
    assert rows["total"] == 2
    assert {r["contato_id"] for r in rows["itens"]} == {contact["id"]}
    assert not any(r["selecionada"] for r in rows["itens"])
    receipt = selecionar(c, item, identity).json()
    rows = c.get(root + "/revisao").json()["itens"]
    assert sum(r["selecionada"] for r in rows) == 1
    assert (
        next(r for r in rows if r["identidade_contato_id"] == extra["id"])["selecionada"] is False
    )
    deselected = selecionar(c, receipt, identity, False).json()
    assert not any(r["selecionada"] for r in c.get(root + "/revisao").json()["itens"])
    test_consentimentos.registrar(f)
    bulk = c.put(
        root + "/selecoes-elegiveis",
        json={"operacao_id": str(uuid4()), "versao": deselected["versao"]},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["selecionados"] == 1
    rows = c.get(root + "/revisao").json()["itens"]
    assert [r["identidade_contato_id"] for r in rows if r["selecionada"]] == [identity["id"]]


def test_selecao_preservada_fora_das_listas_e_referencias_inativas(rascunho):
    c, _, body, contact, identity, lista, _, _ = rascunho
    item = criar(rascunho)
    selected = selecionar(c, item, identity).json()
    assert c.delete("/listas/" + lista["id"] + "/contatos/" + contact["id"]).status_code == 204
    rows = c.get("/campanhas/" + item["id"] + "/revisao").json()
    assert rows["total"] == 1
    row = rows["itens"][0]
    assert row["selecionada"] and not row["pertence_listas"] and not row["candidata_atual"]
    assert selecionar(c, selected, identity).status_code == 409
    # A conta desativada deve ser visível como problema; desmarcar continua permitido.
    c.patch(
        "/contas-envio/" + body["conta_envio_id"],
        json={"operacao_id": str(uuid4()), "versao": 1, "ativo": False},
    )
    assert "conta_inativa" in c.get("/campanhas/" + item["id"]).json()["referencias"]["problemas"]
    assert selecionar(c, selected, identity, False).status_code == 200


def test_consentimento_sempre_atual_bloqueios_e_arquivamento(rascunho):
    c, _, _, contact, _, _, _, f = rascunho
    item = criar(rascunho)
    path = "/campanhas/" + item["id"] + "/revisao"

    def avaliacao():
        return c.get(path).json()["itens"][0]["consentimento"]

    assert avaliacao()["estado"] == "desconhecido"
    test_consentimentos.registrar(f)
    assert avaliacao()["elegivel_consentimento"]
    test_consentimentos.registrar(f, "revogou")
    assert "categoria_revogada" in avaliacao()["motivos"]
    test_consentimentos.registrar(f, "bloqueio_canal", categoria=None)
    assert "bloqueio_canal" in avaliacao()["motivos"]
    test_consentimentos.registrar(f, "bloqueio_global", categoria=None, identity=False)
    assert "bloqueio_global" in avaliacao()["motivos"]
    assert (
        c.patch("/contatos/" + contact["id"], json={"versao": 1, "ativo": False}).status_code == 200
    )
    assert "contato_arquivado" in avaliacao()["motivos"]
    result = c.get(path).json()
    assert "frequencia" in result["condicoes_nao_avaliadas"]
    assert "pronto_envio" not in json.dumps(result)


@pytest.mark.parametrize(
    "change", ["categoria", "escopo_id", "template_versao_id", "conta_envio_id", "listas"]
)
def test_referencias_invalidas_sem_efeito_parcial(rascunho, change):
    c, app, _, _, _, _, _, _ = rascunho
    item = criar(rascunho)
    invalid = (
        "eventos_grupos"
        if change == "categoria"
        else [str(uuid4())]
        if change == "listas"
        else str(uuid4())
    )
    original = c.get("/campanhas/" + item["id"]).json()
    assert (
        c.patch(
            "/campanhas/" + item["id"],
            json={
                "operacao_id": str(uuid4()),
                "versao": 1,
                "nome": "Não salvar",
                change: invalid,
            },
        ).status_code
        == 409
    )
    assert c.get("/campanhas/" + item["id"]).json() == original
    with app.state.contatos.repo.transacao() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(Auditoria).where(Auditoria.entidade == "campanha")
            )
            == 1
        )


def test_plataforma_telegram_sem_identidades_ficticias_e_troca_de_conta(rascunho):
    c, _, _, _, identity, _, _, _ = rascunho
    item = criar(rascunho)
    selected = selecionar(c, item, identity).json()
    conta = test_contas_envio.criar(c)
    template = test_templates.criar(c, plataforma="telegram")
    root = "/campanhas/" + item["id"]
    assert (
        c.patch(
            root,
            json={
                "operacao_id": str(uuid4()),
                "versao": selected["versao"],
                "conta_envio_id": conta["id"],
            },
        ).status_code
        == 409
    )
    response = c.patch(
        root,
        json={
            "operacao_id": str(uuid4()),
            "versao": selected["versao"],
            "conta_envio_id": conta["id"],
            "template_versao_id": template["template_versao_id"],
        },
    )
    assert response.status_code == 200, response.text
    row = c.get(root + "/revisao").json()["itens"][0]
    assert row["selecionada"] and not row["candidata_atual"]


def test_concorrencia_cas_e_rollback_apos_auditoria(rascunho, monkeypatch):
    c, app, _, _, _, _, token, _ = rascunho
    item = criar(rascunho)
    root = "/campanhas/" + item["id"]

    def editar(number):
        return c.patch(
            root, json={"operacao_id": str(uuid4()), "versao": 1, "nome": str(number)}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(editar, range(2))) == [200, 409]
    before = c.get(root).json()
    data = RascunhoEdicao(operacao_id=uuid4(), versao=2, nome="Falhar", listas=[])

    def fail(_mapper, _connection, target):
        if target.id == str(data.operacao_id):
            raise RuntimeError("auditoria")

    event.listen(Auditoria, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="auditoria"):
            app.state.campanhas.editar(item["id"], data, token)
    finally:
        event.remove(Auditoria, "after_insert", fail)
    assert c.get(root).json() == before
    result = app.state.campanhas.editar(item["id"], data, token)
    assert app.state.campanhas.editar(item["id"], data, token) == result


@pytest.mark.parametrize("acao", ["criar", "editar", "selecionar", "replay"])
@pytest.mark.parametrize("invalidacao", ["logout", "expiracao"])
def test_sessao_revalidada_apos_lock(rascunho, monkeypatch, acao, invalidacao):
    c, app, body, _, identity, _, _, _ = rascunho
    item = criar(rascunho)
    root = "/campanhas/" + item["id"]
    method, path, payload = {
        "criar": (c.post, "/campanhas", {**body, "operacao_id": str(uuid4())}),
        "replay": (c.post, "/campanhas", body),
        "editar": (c.patch, root, {"operacao_id": str(uuid4()), "versao": 1, "nome": "Outro"}),
        "selecionar": (
            c.put,
            root + "/selecoes",
            {
                "operacao_id": str(uuid4()),
                "versao": 1,
                "identidade_contato_id": identity["id"],
                "selecionada": True,
            },
        ),
    }[acao]
    repo = app.state.contatos.repo
    lock, waiting = repo.lock, Event()

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            waiting.set()
            return lock.acquire(*args, **kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    monkeypatch.setattr(repo, "lock", ObservedLock())
    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(method, path, json=payload)
            assert waiting.wait(10)
            if invalidacao == "logout":
                assert c.post("/auth/logout").status_code == 204
            else:
                stamp = datetime.now(UTC) + timedelta(hours=9)
                monkeypatch.setattr(app.state.auth, "clock", lambda: stamp)
        assert future.result(10).status_code == 401
    with repo.transacao() as s:
        assert s.scalar(select(Campanha.versao)) == 1
        assert s.scalar(select(func.count()).select_from(CampanhaSelecao)) == 0
        assert s.scalar(select(func.count()).select_from(Campanha)) == 1


def test_fronteira_operador_sem_credenciais_previa_transitoria_e_sem_rede(rascunho, monkeypatch):
    c, app, body, _, _, _, _, _ = rascunho

    def proibido(*args, **kwargs):
        raise AssertionError("Rede externa não autorizada")

    monkeypatch.setattr(socket, "create_connection", proibido)
    monkeypatch.setattr(socket.socket, "connect", proibido)
    with app.state.persistence.database.transaction() as s:
        s.execute(text("UPDATE usuarios SET perfil='operador'"))
    assert c.get("/contas-envio").status_code == 403
    for tipo in ("contas", "escopos", "listas", "versoes"):
        response = c.get("/campanhas/opcoes", params={"tipo": tipo})
        assert response.status_code == 200
        assert "segredo" not in response.text and "conteudo" not in response.text
    item = criar(rascunho)
    root = "/campanhas/" + item["id"]
    response = c.post(root + "/previa", json={"versao": 1, "nome": "Fictício {{nome}}"})
    assert response.status_code == 200, response.text
    assert "Fictício {{nome}}" in response.json()["conteudo_renderizado"]
    assert response.json()["transitoria"]
    assert c.post(root + "/previa", json={"versao": 2, "nome": "Outro"}).status_code == 409
    for endpoint in ("/campanhas", root + "/revisao", "/campanhas/opcoes?tipo=contas"):
        assert c.get(endpoint).headers["cache-control"] == "no-store"
    assert c.post("/campanhas", content=b"x" * 32769).status_code == 413
    assert c.get("/campanhas", params={"limite": 51}).status_code == 422
    assert c.post("/campanhas", json={**body, "status": "preparada"}).status_code == 422
    assert (
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 1, "nome": None}).status_code
        == 422
    )
    del c.headers["X-eDisk-Session"]
    assert c.post("/campanhas", content="não json").status_code == 401
    del c.headers["Authorization"]
    assert c.get("/campanhas").status_code == 401


@pytest.mark.parametrize("nome", ["", " ", "   ", " \t\r\n ", "\u00a0"])
def test_previa_nome_em_branco_retorna_422(rascunho, nome):
    c, *_ = rascunho
    item = criar(rascunho)
    response = c.post("/campanhas/" + item["id"] + "/previa", json={"versao": 1, "nome": nome})
    assert response.status_code == 422, response.text
    assert response.json()["codigo"] == "validacao"


def test_previa_nome_valido_literal_e_opcional_preservados(rascunho):
    c, *_ = rascunho
    item = criar(rascunho)
    root = "/campanhas/" + item["id"]
    nome = "  Fictício {{nome}}  "
    response = c.post(root + "/previa", json={"versao": 1, "nome": nome})
    assert response.status_code == 200, response.text
    assert response.json()["conteudo_renderizado"] == test_templates.entrada()["conteudo"].replace(
        "{{nome}}", nome
    )
    assert response.json()["transitoria"] is True

    template = test_templates.criar(c, conteudo="Texto sem variável.")
    changed = c.patch(
        root,
        json={
            "operacao_id": str(uuid4()),
            "versao": 1,
            "template_versao_id": template["template_versao_id"],
        },
    )
    assert changed.status_code == 200, changed.text
    for fields in ({}, {"nome": None}):
        response = c.post(root + "/previa", json={"versao": 2, **fields})
        assert response.status_code == 200, response.text
        assert response.json()["conteudo_renderizado"] == "Texto sem variável."


def test_revisao_8000_lote_paginas_estaveis_e_sem_consulta_segredo(rascunho):
    c, app, _, _, _, lista, token, _ = rascunho
    item = criar(rascunho)
    user = app.state.auth.current(token).usuario.id
    stamp = agora()
    contacts, phones, identities, members = [], [], [], []
    for n in range(8000):
        cid, pid, iid = str(uuid4()), str(uuid4()), str(uuid4())
        contacts.append(
            dict(
                id=cid,
                nome=f"Pessoa {n:05}",
                nome_busca=f"pessoa {n:05}",
                ativo=1,
                versao=1,
                criado_em=stamp,
                atualizado_em=stamp,
            )
        )
        phones.append(
            dict(
                id=pid,
                contato_id=cid,
                telefone_e164=f"+55119{n:08}",
                ativo=1,
                versao=1,
                criado_em=stamp,
                atualizado_em=stamp,
            )
        )
        identities.append(
            dict(
                id=iid,
                contato_id=cid,
                telefone_id=pid,
                canal="whatsapp",
                criado_por_usuario_id=user,
                criado_em=stamp,
            )
        )
        members.append(dict(lista_id=lista["id"], contato_id=cid, adicionado_em=stamp))
    with app.state.contatos.repo.transacao() as s:
        s.execute(insert(Contato), contacts)
        s.execute(insert(TelefoneContato), phones)
        s.execute(insert(IdentidadeContato), identities)
        s.execute(insert(ListaContato), members)
    queries = []

    def observe(_conn, _cursor, statement, *_):
        queries.append(statement)

    engine = app.state.persistence.database.engine
    event.listen(engine, "before_cursor_execute", observe)
    try:
        first = c.get("/campanhas/" + item["id"] + "/revisao", params={"limite": 50}).json()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert first["total"] == 8001
    assert len(first["itens"]) == 50
    assert len(queries) <= 14, len(queries)
    # O custo HTTP/autenticação é fixo: uma página com uma identidade
    # deve executar a mesma quantidade de consultas que a página com 8.001.
    large_count = len(queries)
    queries.clear()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        c.get("/campanhas/" + item["id"] + "/revisao", params={"limite": 1})
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert len(queries) == large_count
    assert all("segredo_ref" not in sql for sql in queries)
    again = c.get("/campanhas/" + item["id"] + "/revisao", params={"limite": 50}).json()
    assert first["itens"] == again["itens"]
    second = c.get(
        "/campanhas/" + item["id"] + "/revisao", params={"pagina": 2, "limite": 50}
    ).json()
    assert not (
        {r["identidade_contato_id"] for r in first["itens"]}
        & {r["identidade_contato_id"] for r in second["itens"]}
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE campanhas SET status='preparada'",
        "UPDATE campanhas SET versao=0",
        "UPDATE campanhas SET categoria='eventos_grupos'",
        "DELETE FROM campanhas",
        "INSERT OR REPLACE INTO campanhas SELECT * FROM campanhas",
        "INSERT INTO campanha_selecoes SELECT campanha_id,identidade_contato_id,"
        "selecionado_em FROM campanha_selecoes",
    ],
)
def test_constraints_e_historico(rascunho, sql):
    c, app, _, _, identity, _, _, _ = rascunho
    item = criar(rascunho)
    assert selecionar(c, item, identity).status_code == 200
    with pytest.raises(IntegrityError), app.state.persistence.database.transaction() as s:
        s.execute(text(sql))


def test_replay_selecao_listas_removidas_e_filtros(rascunho):
    c, _, _, _, identity, _, _, _ = rascunho
    item = criar(rascunho)
    op = str(uuid4())
    selected = selecionar(c, item, identity, operacao_id=op)
    assert selected.status_code == 200
    assert selecionar(c, item, identity, operacao_id=op).json() == selected.json()
    assert selecionar(c, item, identity, False, operacao_id=op).status_code == 409
    root = "/campanhas/" + item["id"]
    assert (
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 2, "listas": []}).status_code
        == 200
    )
    row = c.get(root + "/revisao", params={"somente_selecionadas": True}).json()["itens"][0]
    assert row["selecionada"] and not row["candidata_atual"]
    assert c.get("/campanhas", params={"q": "%"}).json()["total"] == 0
    assert c.get("/campanhas", params={"pagina": 2}).json()["itens"] == []


@pytest.mark.parametrize("referencia", ["conta", "template", "lista"])
def test_referencia_desativada_rejeita_edicao_mas_preserva_consulta(rascunho, referencia):
    c, _, body, _, identity, lista, _, _ = rascunho
    item = criar(rascunho)
    assert selecionar(c, item, identity).status_code == 200
    root = "/campanhas/" + item["id"]
    detail = c.get(root).json()
    if referencia == "conta":
        path, fields = "/contas-envio/" + body["conta_envio_id"], {"ativo": False}
    elif referencia == "template":
        from app.templates.models import TemplateVersao

        with rascunho[1].state.contatos.repo.transacao() as s:
            parent = s.get(TemplateVersao, body["template_versao_id"]).template_id
        path, fields = "/templates/" + parent, {"ativo": False}
    else:
        path, fields = "/listas/" + lista["id"], {"ativo": False}
    assert (
        c.patch(
            path,
            json={
                **({"operacao_id": str(uuid4())} if referencia != "lista" else {}),
                "versao": 1,
                **fields,
            },
        ).status_code
        == 200
    )
    assert c.get(root).json()["referencias"]["problemas"]
    assert (
        c.patch(
            root, json={"operacao_id": str(uuid4()), "versao": 2, "nome": "Inválido"}
        ).status_code
        == 409
    )
    assert c.get(root).json()["nome"] == detail["nome"]
    assert c.get(root + "/revisao").json()["itens"][0]["selecionada"]
    assert selecionar(c, {"id": item["id"], "versao": 2}, identity, False).status_code == 200


def test_rollback_criacao_e_selecao_sem_reaplicar_efeitos(rascunho, monkeypatch):
    c, app, body, _, identity, _, token, _ = rascunho
    data = RascunhoEntrada(**body)

    def fail(_mapper, _connection, target):
        if target.entidade == "campanha":
            raise RuntimeError("falha depois da escrita")

    event.listen(Auditoria, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="falha depois da escrita"):
            app.state.campanhas.criar(data, token)
    finally:
        event.remove(Auditoria, "after_insert", fail)
    assert c.get("/campanhas").json()["total"] == 0
    item = criar(rascunho)

    from app.campanhas.schemas import SelecaoEntrada

    selection = SelecaoEntrada(
        operacao_id=uuid4(), versao=1, identidade_contato_id=identity["id"], selecionada=True
    )
    service = app.state.campanhas
    original = service._registrar
    observado = []

    def confirmar_sql(connection):
        # Consultas pela Connection, sem recorrer ao identity map do ORM.
        assert (
            connection.execute(
                select(CampanhaSelecao.__table__).where(
                    CampanhaSelecao.campanha_id == item["id"],
                    CampanhaSelecao.identidade_contato_id == identity["id"],
                )
            )
            .one()
            .identidade_contato_id
            == identity["id"]
        )
        assert (
            connection.execute(
                select(Campanha.versao).where(Campanha.id == item["id"])
            ).scalar_one()
            == 2
        )

    def registrar_apos_sql(s, data, usuario, digest, acao, identifier, versao):
        assert data.operacao_id == selection.operacao_id
        # Força o INSERT da seleção antes de criar a auditoria. A ordem de
        # flush entre mappers não determina o ponto da falha deste teste.
        s.flush()
        confirmar_sql(s.connection())
        assert (
            s.connection()
            .execute(select(Auditoria.id).where(Auditoria.id == str(selection.operacao_id)))
            .scalar_one_or_none()
            is None
        )
        observado.append("selecao_escrita")
        return original(s, data, usuario, digest, acao, identifier, versao)

    def falhar_auditoria(_mapper, connection, target):
        if target.id == str(selection.operacao_id):
            confirmar_sql(connection)
            assert (
                connection.execute(
                    select(Auditoria.id).where(Auditoria.id == target.id)
                ).scalar_one()
                == target.id
            )
            observado.append("auditoria_escrita")
            raise RuntimeError("falha após INSERT da seleção e da auditoria")

    with service.repo.transacao() as s:
        auditoria_anterior = s.execute(select(Auditoria.__table__).order_by(Auditoria.id)).all()
    event.listen(Auditoria, "after_insert", falhar_auditoria)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, "_registrar", registrar_apos_sql)
            with pytest.raises(RuntimeError, match="após INSERT"):
                service.selecionar(item["id"], selection, token)
    finally:
        event.remove(Auditoria, "after_insert", falhar_auditoria)

    assert observado == ["selecao_escrita", "auditoria_escrita"]
    with service.repo.transacao() as s:
        assert s.scalar(select(Campanha.versao).where(Campanha.id == item["id"])) == 1
        assert (
            s.scalar(
                select(func.count())
                .select_from(CampanhaSelecao)
                .where(CampanhaSelecao.campanha_id == item["id"])
            )
            == 0
        )
        assert (
            s.execute(select(Auditoria.__table__).order_by(Auditoria.id)).all()
            == auditoria_anterior
        )
        # A auditoria é também o recibo de idempotência: não pode sobreviver à falha.
        assert s.get(Auditoria, str(selection.operacao_id)) is None
    row = c.get("/campanhas/" + item["id"] + "/revisao").json()
    assert row["versao"] == 1 and not row["itens"][0]["selecionada"]
    result = service.selecionar(item["id"], selection, token)
    assert result["versao"] == 2
    assert service.selecionar(item["id"], selection, token) == result
    with service.repo.transacao() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(CampanhaSelecao)
                .where(CampanhaSelecao.campanha_id == item["id"])
            )
            == 1
        )
        assert s.scalar(select(func.count()).select_from(Auditoria)) == len(auditoria_anterior) + 1
        assert s.get(Auditoria, str(selection.operacao_id)) is not None
