"""Fotografias reais, CNG/DPAPI Windows e concorrencia deterministica."""

import json
import os
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
import test_campanhas as campanhas
import test_consentimentos as consent
import test_importacoes as imports
from alembic import command
from sqlalchemy import event, func, insert, select, text

from app.campanhas import preparacoes as mod
from app.campanhas.models import (
    AutorizacaoEnvioWhatsapp,
    Campanha,
    CampanhaSelecao,
    ContextoCampanhas,
    Preparacao,
    PreparacaoItem,
    TentativaEnvioWhatsapp,
)
from app.consentimentos.models import EventoConsentimento, IdentidadeContato, PreferenciaContato
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria, Contato, ListaContato, TelefoneContato
from app.contatos.repository import agora
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision
from app.provedores.whatsapp_baileys import ResultadoEnvioWhatsapp

cadastro = campanhas.cadastro
consentimento = campanhas.consentimento
rascunho = campanhas.rascunho
pytestmark = pytest.mark.skipif(os.name != "nt", reason="CNG e DPAPI reais")


def op(**fields):
    return {"operacao_id": str(uuid4()), **fields}


@pytest.fixture
def pronta(rascunho):
    c, app, _, contact, identity, _, token, f = rascunho
    consent.registrar(f)
    item = campanhas.criar(rascunho)
    selected = campanhas.selecionar(c, item, identity)
    assert selected.status_code == 200
    init = c.post("/campanhas/chaves/inicializar", json=op())
    assert init.status_code == 200, init.text
    code = init.json()["codigo_recuperacao"]
    assert c.post("/campanhas/chaves/conferir", json=op(codigo=code)).status_code == 200
    return rascunho, item["id"], code


def entrada(c, identifier):
    review = c.get(f"/campanhas/{identifier}/revisao-preparacao")
    assert review.status_code == 200, review.text
    value = review.json()
    return op(versao=value["versao"], marcador=value["marcador"])


def preparar(f):
    r, identifier, _ = f
    body = entrada(r[0], identifier)
    result = r[0].post(f"/campanhas/{identifier}/preparacoes", json=body)
    assert result.status_code == 201, result.text
    return result.json(), body


class WhatsappControlado:
    def __init__(self, resultado=None, erro=None):
        self.resultado = resultado or ResultadoEnvioWhatsapp(
            "confirmada", "aceita", "BAILEYS-MESSAGE-1"
        )
        self.erro = erro
        self.chamadas = 0

    def enviar(self, conta_id, destino_e164, texto, tentativa_id):
        self.chamadas += 1
        assert conta_id and tentativa_id
        assert destino_e164 == "+5511912345678"
        assert texto.strip()
        if self.erro:
            raise self.erro
        return self.resultado


def preparar_whatsapp(pronta):
    r, identifier, _ = pronta
    c = r[0]
    receipt, _ = preparar(pronta)
    prep = receipt["preparacao_id"]
    root = f"/campanhas/{identifier}"
    snapshot = c.get(root + f"/preparacoes/{prep}").json()
    confirmed = c.post(
        root + f"/preparacoes/{prep}/confirmacao",
        json=op(
            versao=receipt["versao"],
            preparacao_id=prep,
            impressao=snapshot["impressao"],
        ),
    )
    assert confirmed.status_code == 200, confirmed.text
    review = c.get(root + f"/preparacoes/{prep}/revisao-envio-whatsapp")
    assert review.status_code == 200, review.text
    value = review.json()
    body = op(
        versao=value["versao"],
        impressao=value["impressao"],
        marcador_envio=value["marcador_envio"],
        confirmo_envio_real=True,
    )
    return root, prep, body


def test_whatsapp_idempotencia_confirmacao_repetida(pronta):
    c, app = pronta[0][0], pronta[0][1]
    root, prep, body = preparar_whatsapp(pronta)
    transporte = WhatsappControlado()
    app.state.envios_whatsapp.whatsapp = transporte
    first = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    replay = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    assert first.status_code == 201, first.text
    assert replay.json() == first.json()
    assert first.json()["estado"] == "confirmada"
    assert first.json()["aceitacao_nao_e_entrega"] is True
    assert transporte.chamadas == 1
    with app.state.contatos.repo.transacao() as session:
        assert session.scalar(select(func.count()).select_from(AutorizacaoEnvioWhatsapp)) == 1
        tentativa = session.scalars(select(TentativaEnvioWhatsapp)).one()
        assert tentativa.message_id == "BAILEYS-MESSAGE-1"


def test_whatsapp_timeout_fica_desconhecido_sem_reenvio(pronta):
    c, app = pronta[0][0], pronta[0][1]
    root, prep, body = preparar_whatsapp(pronta)
    transporte = WhatsappControlado(erro=TimeoutError())
    app.state.envios_whatsapp.whatsapp = transporte
    first = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    replay = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    assert first.status_code == 201, first.text
    assert replay.json() == first.json()
    assert (first.json()["estado"], first.json()["codigo_resultado"]) == (
        "desconhecida",
        "sem_confirmacao",
    )
    assert first.json()["reenvio_automatico"] is False
    assert transporte.chamadas == 1


def test_whatsapp_migracao_e_recuperacao_preservam_historico(pronta, monkeypatch):
    c, app = pronta[0][0], pronta[0][1]
    root, prep, body = preparar_whatsapp(pronta)
    db = app.state.persistence.database
    tables = (
        "campanhas",
        "campanha_preparacoes",
        "campanha_preparacao_itens",
        "eventos_consentimento",
        "preferencias_contato",
        "auditoria",
    )
    with db.engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in tables
        }
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0018_telegram_executor_lote")
        command.upgrade(config, "0019_whatsapp_envio_unitario")
        after = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in tables
        }
        assert after == before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)]
    assert read_revision(app.state.persistence.settings.database_path) == (
        "0019_whatsapp_envio_unitario"
    )
    transporte = WhatsappControlado()
    app.state.envios_whatsapp.whatsapp = transporte
    service = app.state.envios_whatsapp
    persistir = service._persistir_resultado

    def falhar(*_args):
        raise NegocioError("persistencia_indisponivel", "Falha controlada.", 503)

    monkeypatch.setattr(service, "_persistir_resultado", falhar)
    response = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["estado"] == "desconhecida"
    with app.state.contatos.repo.transacao() as session:
        assert session.scalars(select(TentativaEnvioWhatsapp)).one().estado == "iniciada"
    monkeypatch.setattr(service, "_persistir_resultado", persistir)
    service.recuperar()
    replay = c.post(root + f"/preparacoes/{prep}/envio-whatsapp", json=body)
    assert replay.json()["estado"] == "desconhecida"
    assert replay.json()["reenvio_automatico"] is False
    assert transporte.chamadas == 1
    with app.state.contatos.repo.transacao() as session:
        tentativa = session.scalars(select(TentativaEnvioWhatsapp)).one()
        assert tentativa.estado == "desconhecida"
        assert session.scalar(select(func.count()).select_from(AutorizacaoEnvioWhatsapp)) == 1


def test_ciclo_cifrado_confirmacao_historico_replay(pronta, caplog):
    r, identifier, code = pronta
    c, app, _, contact, identity, _, token, f = r
    receipt, body = preparar(pronta)
    root = f"/campanhas/{identifier}"
    p = receipt["preparacao_id"]
    snapshot = c.get(root + f"/preparacoes/{p}").json()
    read = c.get(root + f"/preparacoes/{p}/itens/{identity['id']}/conteudo")
    assert read.status_code == 200, read.text
    assert read.headers["cache-control"] == "no-store"
    assert c.patch(root, json=op(versao=receipt["versao"], nome="Nao")).status_code == 409
    assert campanhas.selecionar(c, receipt, identity, False).status_code == 409
    confirm = op(versao=receipt["versao"], preparacao_id=p, impressao=snapshot["impressao"])
    conf = c.post(root + f"/preparacoes/{p}/confirmacao", json=confirm)
    assert conf.status_code == 200, conf.text
    consent.registrar(f, "revogou")
    current = c.get(root + f"/preparacoes/{p}").json()
    assert current["itens"][0]["incluido"]
    assert "categoria_revogada" in current["itens"][0]["impedimentos_atuais"]
    ret = c.post(root + "/retorno-rascunho", json=op(versao=conf.json()["versao"], preparacao_id=p))
    assert ret.status_code == 200, ret.text
    assert c.post(root + "/preparacoes", json=body).json() == receipt
    assert c.post(root + f"/preparacoes/{p}/confirmacao", json=confirm).json() == conf.json()
    assert c.get(root).json()["status"] == "rascunho"
    assert c.get(root + f"/preparacoes/{p}").json()["invalidado_em"]
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Preparacao)) == 1
        obj = s.scalars(select(PreparacaoItem)).one()
        value = read.json()["conteudo_renderizado"].encode()
        assert value not in obj.conteudo_cifrado
        audits = "\n".join(s.scalars(select(Auditoria.resumo_json)).all())
        assert read.json()["conteudo_renderizado"] not in audits
        assert code not in audits
    assert code not in caplog.text


@pytest.mark.parametrize(
    "mutation",
    [
        "contato",
        "telefone",
        "lista",
        "pertencimento",
        "consentimento",
        "template",
        "conta",
        "campanha",
        "selecao",
        "importacao",
    ],
)
def test_marcador_cobre_mutacoes(pronta, mutation):
    r, identifier, _ = pronta
    c, app, _, contact, identity, lista, token, f = r
    body = entrada(c, identifier)
    if mutation == "contato":
        version = c.get("/contatos/" + contact["id"]).json()["versao"]
        response = c.patch("/contatos/" + contact["id"], json={"versao": version, "nome": "Mudou"})
    elif mutation == "telefone":
        response = c.post(
            "/contatos/" + contact["id"] + "/telefones", json={"telefone": "+442083661177"}
        )
    elif mutation == "lista":
        response = c.patch("/listas/" + lista["id"], json={"versao": 1, "nome": "Mudou"})
    elif mutation == "pertencimento":
        response = c.delete("/listas/" + lista["id"] + "/contatos/" + contact["id"])
    elif mutation == "consentimento":
        consent.registrar(f, "revogou")
        response = None
    elif mutation == "template":
        t = c.get("/templates").json()["itens"][0]
        response = c.patch("/templates/" + t["id"], json=op(versao=t["versao"], nome="Mudou"))
    elif mutation == "conta":
        a = c.get("/contas-envio").json()["itens"][0]
        response = c.patch("/contas-envio/" + a["id"], json=op(versao=a["versao"], nome="Mudou"))
    elif mutation == "campanha":
        response = c.patch("/campanhas/" + identifier, json=op(versao=body["versao"], nome="Mudou"))
    elif mutation == "selecao":
        response = campanhas.selecionar(
            c, {"id": identifier, "versao": body["versao"]}, identity, False
        )
    else:
        imp = imports.preparar((c, app, token), b"Nome;Telefone\nNovo;+442083661177\n")
        response = imports.confirmar(c, imp[0])
    if response is not None:
        assert response.status_code < 300, response.text
    assert c.post("/campanhas/" + identifier + "/preparacoes", json=body).status_code == 409


def test_recuperacao_preserva_cifras_e_codigo_incorreto(pronta):
    r, identifier, code = pronta
    c, app, _, _, identity, _, token, _ = r
    receipt, _ = preparar(pronta)
    root = f"/campanhas/{identifier}/preparacoes/{receipt['preparacao_id']}"
    with app.state.contatos.repo.transacao() as s:
        p = s.get(Preparacao, receipt["preparacao_id"])
        before = (p.manifesto, p.chave_cifrada, p.impressao)
        ctx = s.get(ContextoCampanhas, "campanhas-v1")
        ctx.chave_dpapi = b"danificado"
    assert c.get(root + f"/itens/{identity['id']}/conteudo").status_code == 503
    assert (
        c.post(
            "/campanhas/chaves/recuperar",
            json=op(codigo=code[:-1] + ("A" if code[-1] != "A" else "B")),
        ).status_code
        == 503
    )
    result = c.post("/campanhas/chaves/recuperar", json=op(codigo=code))
    assert result.status_code == 200, result.text
    assert c.get(root + f"/itens/{identity['id']}/conteudo").status_code == 200
    with app.state.contatos.repo.transacao() as s:
        p = s.get(Preparacao, receipt["preparacao_id"])
        assert before == (p.manifesto, p.chave_cifrada, p.impressao)


@pytest.mark.parametrize("action", ["UPDATE", "DELETE", "REPLACE"])
def test_auditoria_append_only(pronta, action):
    _, identifier, _ = pronta
    app = pronta[0][1]
    with pytest.raises(mod.NegocioError):
        with app.state.contatos.repo.transacao() as s:
            if action == "UPDATE":
                s.execute(text("UPDATE auditoria SET acao='mudou'"))
            elif action == "DELETE":
                s.execute(text("DELETE FROM auditoria"))
            else:
                s.execute(text("INSERT OR REPLACE INTO auditoria SELECT * FROM auditoria LIMIT 1"))


@pytest.mark.parametrize("failure", ["item", "auditoria", "commit"])
def test_rollback_apos_escrita(pronta, failure):
    r, identifier, _ = pronta
    c, app, *_ = r
    body = entrada(c, identifier)

    def fail(*args):
        if (
            failure == "commit"
            and not args[0].exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar()
        ):
            return
        raise RuntimeError("falha de teste")

    target, name = (
        (PreparacaoItem, "after_insert") if failure == "item" else (Auditoria, "after_insert")
    )
    if failure == "item":
        target, name = app.state.persistence.database.engine, "after_cursor_execute"

        def fail(conn, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO campanha_preparacao_itens"):
                raise RuntimeError("falha apos escrita")

    if failure == "commit":
        target, name = app.state.persistence.database.engine, "commit"
    event.listen(target, name, fail)
    try:
        assert c.post("/campanhas/" + identifier + "/preparacoes", json=body).status_code == 500
    finally:
        event.remove(target, name, fail)
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Preparacao)) == 0
        assert s.scalar(select(func.count()).select_from(PreparacaoItem)) == 0
        assert s.get(Campanha, identifier).status == "rascunho"
        assert s.get(Auditoria, body["operacao_id"]) is None


def test_revogacao_durante_cifragem_sem_lock(pronta, monkeypatch):
    r, identifier, _ = pronta
    c, app, _, _, _, _, _, f = r
    body = entrada(c, identifier)
    reached, release = Event(), Event()
    original = mod.previa

    def pause(*args):
        reached.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(mod, "previa", pause)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(c.post, "/campanhas/" + identifier + "/preparacoes", json=body)
        assert reached.wait(10)
        consent.registrar(f, "revogou")
        release.set()
        assert pending.result().status_code == 409


def test_preparacoes_simultaneas_e_replay(pronta):
    r, identifier, _ = pronta
    c = r[0]
    body = entrada(c, identifier)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: c.post("/campanhas/" + identifier + "/preparacoes", json=body), range(2)
            )
        )
    assert [x.status_code for x in results] == [201, 201]
    assert results[0].json() == results[1].json()


@pytest.mark.parametrize("change", ["texto", "total", "itens"])
def test_limites_atomicos(pronta, monkeypatch, change):
    r, identifier, _ = pronta
    name = {"texto": "MAX_TEXTO", "total": "MAX_TOTAL", "itens": "MAX_ITENS"}[change]
    monkeypatch.setattr(mod, name, 0)
    response = (
        r[0].post("/campanhas/" + identifier + "/preparacoes", json=entrada(r[0], identifier))
        if change != "itens"
        else r[0].get("/campanhas/" + identifier + "/revisao-preparacao")
    )
    assert response.status_code == 422


def test_volume_8000(pronta, capsys, monkeypatch):
    r, identifier, _ = pronta
    c, app, _, contact, identity, lista, token, f = r
    now = agora()
    with app.state.contatos.repo.transacao() as s:
        preference = s.get(PreferenciaContato, (f[4]["id"], identity["id"], "promocoes"))
        contacts, phones, ids, prefs, sels, groups, events = [], [], [], [], [], [], []
        original = s.get(EventoConsentimento, preference.evento_origem_id)
        event_fields = {
            col.name: getattr(original, col.name) for col in EventoConsentimento.__table__.columns
        }
        user = s.get(Campanha, identifier).criado_por_usuario_id
        # Uma identidade por contato; mesmas FKs e consentimento real da base de teste.
        for n in range(7999):
            cid, pid, iid = [str(uuid4()) for _ in range(3)]
            eid = str(uuid4())
            events.append(
                {
                    **event_fields,
                    "id": eid,
                    "contato_id": cid,
                    "identidade_contato_id": iid,
                    "registrado_em": now,
                    "requisicao_hash": uuid4().hex * 2,
                }
            )
            contacts.append(
                dict(
                    id=cid,
                    nome="Pessoa de teste",
                    nome_busca="pessoa de teste",
                    observacoes=None,
                    ativo=1,
                    versao=1,
                    criado_em=now,
                    atualizado_em=now,
                    bloqueio_global_em=None,
                )
            )
            phones.append(
                dict(
                    id=pid,
                    contato_id=cid,
                    telefone_e164=f"+55119{n:08d}",
                    ativo=1,
                    versao=1,
                    criado_em=now,
                    atualizado_em=now,
                )
            )
            ids.append(
                dict(
                    id=iid,
                    contato_id=cid,
                    telefone_id=pid,
                    canal="whatsapp",
                    bloqueio_canal_em=None,
                    criado_por_usuario_id=user,
                    criado_em=now,
                )
            )
            prefs.append(
                dict(
                    escopo_id=f[4]["id"],
                    identidade_contato_id=iid,
                    categoria="promocoes",
                    estado="autorizado",
                    evento_origem_id=eid,
                    atualizado_em=now,
                )
            )
            sels.append(dict(campanha_id=identifier, identidade_contato_id=iid, selecionado_em=now))
            groups.append(dict(lista_id=lista["id"], contato_id=cid, adicionado_em=now))
        for model, rows in (
            (Contato, contacts),
            (TelefoneContato, phones),
            (IdentidadeContato, ids),
            (EventoConsentimento, events),
            (PreferenciaContato, prefs),
            (CampanhaSelecao, sels),
            (ListaContato, groups),
        ):
            s.execute(insert(model), rows)
        app.state.contatos.repo.auditar(
            s,
            s.get(Campanha, identifier).criado_por_usuario_id,
            "carga_teste",
            "campanha",
            identifier,
        )
    del contacts, phones, ids, prefs, sels, groups, events
    body = entrada(c, identifier)
    tracemalloc.start()
    start = time.perf_counter()
    response = c.post("/campanhas/" + identifier + "/preparacoes", json=body)
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert response.status_code == 201, response.text
    root = "/campanhas/" + identifier + "/preparacoes/" + response.json()["preparacao_id"]
    original = app.state.preparacoes._integridade
    decrypt = mod.GCM.decrypt
    validations, purposes, verification_seconds = [], [], []

    def counted(*args):
        validations.append(args[1].id)
        started = time.perf_counter()
        try:
            return original(*args)
        finally:
            verification_seconds.append(time.perf_counter() - started)

    def tracked(self, nonce, data, context):
        parsed = json.loads(context)
        purposes.append(parsed["finalidade"] if isinstance(parsed, dict) else "verificador")
        return decrypt(self, nonce, data, context)

    monkeypatch.setattr(app.state.preparacoes, "_integridade", counted)
    monkeypatch.setattr(mod.GCM, "decrypt", tracked)
    for limit in (1, 50):
        validations.clear()
        purposes.clear()
        verification_seconds.clear()
        tracemalloc.start()
        start = time.perf_counter()
        page = c.get(root, params={"limite": limit})
        elapsed = time.perf_counter() - start
        _, page_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert page.status_code == 200, page.text
        snapshot = page.json()
        assert snapshot["incluidos"] == 8000
        assert len(snapshot["itens"]) == limit
        assert validations == [response.json()["preparacao_id"]]
        assert "texto" not in purposes
        assert purposes.count("manifesto") == 1
        with capsys.disabled():
            print(
                f"PAGINA14 destinatarios=8000 limite={limit} verificacoes=1 "
                f"segundos={elapsed:.3f} manifesto_segundos={sum(verification_seconds):.3f} "
                f"pico_python_mib={page_peak / 1048576:.2f}"
            )
    with capsys.disabled():
        print(
            f"VOLUME14 destinatarios=8000 segundos={seconds:.3f} "
            f"pico_python_mib={peak / 1048576:.2f} bytes_texto={snapshot['bytes_texto']}"
        )


@pytest.mark.parametrize("change", ["cipher", "remove", "manifesto", "referencias"])
def test_corrupcao_detectada_sem_reescrever(pronta, change):
    r, identifier, _ = pronta
    c, app, _, _, identity, *_ = r
    receipt, _ = preparar(pronta)
    pid = receipt["preparacao_id"]
    root = f"/campanhas/{identifier}/preparacoes/{pid}"
    snapshot = c.get(root).json()
    # Simula adulteracao fora da aplicacao: somente neste SQLite descartavel.
    with app.state.persistence.database.engine.begin() as conn:
        if change in ("cipher", "remove"):
            trigger = "campanha_preparacao_itens_sem_" + (
                "delete" if change == "remove" else "update"
            )
            conn.exec_driver_sql("DROP TRIGGER " + trigger)
            if change == "remove":
                conn.exec_driver_sql("DELETE FROM campanha_preparacao_itens")
            else:
                data = conn.exec_driver_sql(
                    "SELECT conteudo_cifrado FROM campanha_preparacao_itens"
                ).scalar_one()
                conn.exec_driver_sql(
                    "UPDATE campanha_preparacao_itens SET conteudo_cifrado=?",
                    (data[:-1] + bytes([data[-1] ^ 1]),),
                )
        else:
            conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
            if change == "manifesto":
                data = conn.exec_driver_sql(
                    "SELECT manifesto FROM campanha_preparacoes"
                ).scalar_one()
                conn.exec_driver_sql(
                    "UPDATE campanha_preparacoes SET manifesto=?",
                    (data[:-1] + bytes([data[-1] ^ 1]),),
                )
            else:
                header = json.loads(
                    conn.exec_driver_sql(
                        "SELECT fotografia_json FROM campanha_preparacoes"
                    ).scalar_one()
                )
                header["escopo_id"] = str(uuid4())
                conn.exec_driver_sql(
                    "UPDATE campanha_preparacoes SET fotografia_json=?", (json.dumps(header),)
                )
    result = c.post(
        root + "/confirmacao",
        json=op(versao=receipt["versao"], preparacao_id=pid, impressao=snapshot["impressao"]),
    )
    assert result.status_code == 503, result.text
    with app.state.contatos.repo.transacao() as session:
        assert session.get(Preparacao, pid).confirmado_em is None


@pytest.mark.parametrize(
    "field",
    [
        "base_id",
        "campanha_id",
        "preparacao_id",
        "identidade",
        "conta_envio_id",
        "escopo_id",
        "categoria",
        "template_versao_id",
        "formato",
    ],
)
def test_aad_vincula_contexto_persistido(pronta, field):
    r, identifier, _ = pronta
    receipt, _ = preparar(pronta)
    with r[1].state.contatos.repo.transacao() as s:
        p = s.get(Preparacao, receipt["preparacao_id"])
        ctx = s.get(ContextoCampanhas, "campanhas-v1")
        header = mod.json.loads(p.fotografia_json)
        item = s.scalars(select(PreparacaoItem)).one()
        with mod.GCM(mod.abrir(ctx)) as root:
            key = root.decrypt(p.chave_nonce, p.chave_cifrada, mod.aad(header, "chave"))
        identity = item.identidade_contato_id
        if field == "identidade":
            identity = str(uuid4())
        else:
            header[field] = 2 if field == "formato" else str(uuid4())
        with mod.GCM(key) as g, pytest.raises(mod.NegocioError):
            g.decrypt(item.nonce, item.conteudo_cifrado, mod.aad(header, "texto", identity))


def test_chaves_apresentacao_unica_reatribuicao_e_operador(pronta):
    from sqlalchemy import update

    from app.auth.models import Usuario

    r, identifier, code = pronta
    c, app, *_ = r
    body = op()
    result = c.post("/campanhas/chaves/reemitir", json=body)
    assert result.status_code == 200
    assert result.json()["codigo_recuperacao"] != code
    assert c.post("/campanhas/chaves/reemitir", json=body).status_code == 409
    assert c.get("/campanhas/" + identifier + "/revisao-preparacao").status_code == 409
    assert (
        c.post(
            "/campanhas/chaves/conferir", json=op(codigo=result.json()["codigo_recuperacao"])
        ).status_code
        == 200
    )
    with app.state.contatos.repo.transacao() as s:
        s.execute(update(Usuario).values(perfil="operador"))
    for action in ("inicializar", "reemitir", "conferir", "recuperar"):
        assert c.post("/campanhas/chaves/" + action, content=b"invalido").status_code == 403


def test_reabertura_outro_processo_e_sem_plaintext(pronta):
    import subprocess
    import sys

    r, identifier, code = pronta
    c, app, _, _, identity, *_ = r
    receipt, _ = preparar(pronta)
    url = (
        f"/campanhas/{identifier}/preparacoes/{receipt['preparacao_id']}"
        f"/itens/{identity['id']}/conteudo"
    )
    value = c.get(url).json()["conteudo_renderizado"]
    root = app.state.persistence.settings.root
    for path in root.rglob("*"):
        if path.is_file():
            try:
                data = path.read_bytes()
            except PermissionError:
                assert path.suffix == ".lock" or "lock" in path.name
                continue
            assert value.encode("utf-8") not in data
            assert code.encode("ascii") not in data
    script = """
import sys,sqlite3,json
from types import SimpleNamespace
from app.campanhas.preparacoes import abrir,aad
from app.campanhas.crypto import GCM
c=sqlite3.connect(sys.argv[1]);c.row_factory=sqlite3.Row
ctx=SimpleNamespace(**dict(c.execute('select * from campanha_contexto_criptografico').fetchone()))
p=c.execute('select * from campanha_preparacoes').fetchone();h=json.loads(p['fotografia_json'])
i=c.execute('select * from campanha_preparacao_itens').fetchone()
with GCM(abrir(ctx)) as root:key=root.decrypt(p['chave_nonce'],p['chave_cifrada'],aad(h,'chave'))
with GCM(key) as g:
    value=g.decrypt(i['nonce'],i['conteudo_cifrado'],aad(h,'texto',i['identidade_contato_id']))
assert value==sys.stdin.buffer.read()
print('ok')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(app.state.persistence.settings.database_path)],
        input=value.encode("utf-8"),
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"ok"
    old = (
        entrada(c, identifier)
        if c.get("/campanhas/" + identifier).json()["status"] == "rascunho"
        else None
    )
    assert old is None


@pytest.mark.parametrize("read", [False, True])
def test_expiracao_antes_commit_ou_resposta(pronta, monkeypatch, read):
    from datetime import timedelta

    r, identifier, _ = pronta
    c, app, _, _, identity, *_ = r
    original_clock = app.state.auth.clock
    if read:
        receipt, _ = preparar(pronta)
        original = app.state.preparacoes._integridade

        def expire(*args):
            original(*args)
            monkeypatch.setattr(
                app.state.auth, "clock", lambda: original_clock() + timedelta(hours=9)
            )

        monkeypatch.setattr(app.state.preparacoes, "_integridade", expire)
        response = c.get(
            f"/campanhas/{identifier}/preparacoes/{receipt['preparacao_id']}/itens/{identity['id']}/conteudo"
        )
    else:
        body = entrada(c, identifier)
        original = app.state.preparacoes._recibo

        def expire(*args):
            result = original(*args)
            monkeypatch.setattr(
                app.state.auth, "clock", lambda: original_clock() + timedelta(hours=9)
            )
            return result

        monkeypatch.setattr(app.state.preparacoes, "_recibo", expire)
        response = c.post("/campanhas/" + identifier + "/preparacoes", json=body)
    assert response.status_code == 401, response.text
    if not read:
        with app.state.contatos.repo.transacao() as s:
            assert s.scalar(select(func.count()).select_from(Preparacao)) == 0


def test_exclusoes_listas_revogacao_e_referencias(pronta):
    r, identifier, _ = pronta
    c, app, _, contact, identity, lista, token, f = r
    assert c.delete("/listas/" + lista["id"] + "/contatos/" + contact["id"]).status_code == 204
    review = c.get("/campanhas/" + identifier + "/revisao-preparacao").json()
    assert review["excluidos"] == 1 and "fora_das_listas" in review["itens"][0]["motivos"]
    assert (
        c.post(
            "/campanhas/" + identifier + "/preparacoes",
            json=op(versao=review["versao"], marcador=review["marcador"]),
        ).status_code
        == 409
    )
    assert c.put("/listas/" + lista["id"] + "/contatos/" + contact["id"]).status_code == 204
    consent.registrar(f, "revogou")
    review = c.get("/campanhas/" + identifier + "/revisao-preparacao").json()
    assert review["incluidos"] == 0
    account = c.get("/contas-envio").json()["itens"][0]
    assert (
        c.patch(
            "/contas-envio/" + account["id"], json=op(versao=account["versao"], ativo=False)
        ).status_code
        == 200
    )
    assert c.get("/campanhas/" + identifier + "/revisao-preparacao").status_code == 409


@pytest.mark.parametrize("extra", [0, 1])
def test_limite_utf8_64k_real(pronta, extra):
    r, identifier, _ = pronta
    c, app, _, contact, *_ = r
    current = c.get("/contatos/" + contact["id"]).json()
    assert (
        c.patch(
            "/contatos/" + contact["id"],
            json={"versao": current["versao"], "nome": "\U0001f642" * 120},
        ).status_code
        == 200
    )
    template = c.get("/templates").json()["itens"][0]
    version = c.post(
        "/templates/" + template["id"] + "/versoes",
        json=op(plataforma="whatsapp", conteudo="{{nome}}" * 136 + "x" * (256 + extra)),
    )
    assert version.status_code == 201, version.text
    campaign = c.get("/campanhas/" + identifier).json()
    assert (
        c.patch(
            "/campanhas/" + identifier,
            json=op(
                versao=campaign["versao"], template_versao_id=version.json()["template_versao_id"]
            ),
        ).status_code
        == 200
    )
    response = c.post("/campanhas/" + identifier + "/preparacoes", json=entrada(c, identifier))
    assert response.status_code == (422 if extra else 201), response.text
    with app.state.contatos.repo.transacao() as s:
        if extra:
            assert s.scalar(select(func.count()).select_from(Preparacao)) == 0
        else:
            assert s.scalars(select(Preparacao)).one().bytes_texto == 65536


def test_troca_de_ciphertext_entre_preparacoes(pronta):
    r, identifier, _ = pronta
    c, app, _, _, identity, *_ = r
    first, _ = preparar(pronta)
    root = "/campanhas/" + identifier
    assert (
        c.post(
            root + "/retorno-rascunho",
            json=op(versao=first["versao"], preparacao_id=first["preparacao_id"]),
        ).status_code
        == 200
    )
    second, _ = preparar(pronta)
    url = root + "/preparacoes/" + second["preparacao_id"]
    with app.state.persistence.database.engine.begin() as conn:
        old = conn.exec_driver_sql(
            "SELECT nonce,conteudo_cifrado FROM campanha_preparacao_itens WHERE preparacao_id=?",
            (first["preparacao_id"],),
        ).one()
        conn.exec_driver_sql("DROP TRIGGER campanha_preparacao_itens_sem_update")
        conn.exec_driver_sql(
            "UPDATE campanha_preparacao_itens SET nonce=?,conteudo_cifrado=? WHERE preparacao_id=?",
            (old[0], old[1], second["preparacao_id"]),
        )
    assert c.get(url + "/itens/" + identity["id"] + "/conteudo").status_code == 503


def test_revogacao_de_sessao_durante_renderizacao(pronta, monkeypatch):
    r, identifier, _ = pronta
    c, app, *_ = r
    body = entrada(c, identifier)
    ready, release = Event(), Event()
    original = mod.previa

    def pause(*args):
        ready.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(mod, "previa", pause)
    with ThreadPoolExecutor(2) as pool:
        result = pool.submit(c.post, "/campanhas/" + identifier + "/preparacoes", json=body)
        assert ready.wait(10)
        assert c.post("/auth/logout").status_code == 204
        release.set()
        assert result.result().status_code == 401
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Preparacao)) == 0


def test_downgrade_recusa_chave_existente(pronta):
    from alembic import command

    from app.persistencia.migrations import bundled_migrations, migration_config

    app = pronta[0][1]
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        with (
            bundled_migrations() as location,
            app.state.persistence.database.engine.begin() as conn,
        ):
            config = migration_config(location)
            config.attributes["connection"] = conn
            command.downgrade(config, "0006_campanhas_rascunho")
    assert pronta[0][0].get("/campanhas/chaves/estado").json()["conferida"]


@pytest.mark.parametrize("table", ["campanha_listas", "campanha_selecoes"])
def test_reparentar_associacao_preparada_e_bloqueado(pronta, table):
    from sqlalchemy.exc import IntegrityError

    r, identifier, _ = pronta
    c, app, *_ = r
    preparar(pronta)
    other = campanhas.criar(r, operacao_id=str(uuid4()))
    with pytest.raises(IntegrityError), app.state.persistence.database.transaction() as s:
        s.execute(
            text(f"UPDATE {table} SET campanha_id=:destino WHERE campanha_id=:origem"),
            {"destino": other["id"], "origem": identifier},
        )
