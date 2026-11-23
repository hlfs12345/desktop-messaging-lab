"""Recepção pelo adaptador e lifecycle reais; HTTP controlado, sem rede externa."""

import json
from queue import Queue
from threading import Condition, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_contatos
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from app.consentimentos.models import (
    EventoTelegram,
    IdentidadeTelegram,
    InscricaoConta,
    ReciboTelegram,
)
from app.consentimentos.schemas import EscopoEntrada
from app.contas_envio.models import ContaEnvio
from app.contatos.models import Contato
from app.contatos.repository import agora
from app.main import create_app
from app.persistencia.migrations import (
    bundled_migrations,
    migration_config,
    read_revision,
    schema_validator,
)
from app.provedores.telegram import Telegram
from app.runtime.telegram_recepcao import RecepcaoTelegram

cadastro = test_contatos.cadastro
SECRET = "1001:SENTINELA_TELEGRAM_A"


def update(identifier, command="/start", *, user=12345, private=True):
    return {
        "update_id": identifier,
        "message": {
            "message_id": identifier,
            "date": int(agora().timestamp()),
            "chat": {"id": user, "type": "private" if private else "group"},
            "from": {"id": user, "is_bot": False, "first_name": "PII_NAO_PERSISTIR"},
            "text": command,
        },
    }


class Servidor:
    def __init__(self):
        self.requests, self.updates, self.bots = [], {}, {}
        self.condition, self.release, self.em_rede = Condition(), Event(), Event()
        self.block_token = None
        self.status, self.error, self.replay = 200, None, False

    def wait(self, predicate):
        with self.condition:
            assert self.condition.wait_for(predicate, timeout=5)

    def connection(self, host, timeout):
        assert (host, timeout) == ("api.telegram.org", 5)
        server = self

        class Connection:
            def __init__(self):
                self.cancelled = Event()
                self.sock = self

            def request(self, method, path, body, headers):
                assert method == "POST" and headers == {"Content-Type": "application/json"}
                self.secret, self.method = path[4:].split("/")
                self.params = json.loads(body)
                with server.condition:
                    server.requests.append((self.secret, self.method, self.params))
                    server.condition.notify_all()

            def getresponse(self):
                if self.method == "getUpdates" and self.secret == server.block_token:
                    server.em_rede.set()
                    while not server.release.wait(0.01):
                        if self.cancelled.is_set():
                            raise OSError("corpo privado " + self.secret)
                if server.error is not None:
                    raise server.error("corpo privado " + self.secret)
                bot = server.bots.get(self.secret, int(self.secret.split(":")[0]))
                if self.method == "getMe":
                    self.status = 200
                    result = {
                        "id": bot,
                        "is_bot": True,
                        "first_name": "Bot teste",
                        "username": "bot_teste",
                    }
                elif self.method == "sendMessage":
                    self.status = 200
                    result = {
                        "message_id": len(server.requests),
                        "chat": {"id": self.params["chat_id"]},
                    }
                else:
                    assert self.method == "getUpdates"
                    assert self.params["timeout"] > 0
                    assert self.params["allowed_updates"] == ["message"]
                    self.status = server.status
                    offset = self.params.get("offset", 0)
                    result = [
                        u
                        for u in server.updates.get(bot, [])
                        if server.replay or u["update_id"] >= offset
                    ]
                self.body = json.dumps(
                    {
                        "ok": self.status == 200,
                        "result": result,
                        "description": "corpo privado " + self.secret,
                    }
                ).encode()
                return self

            def read(self, limit):
                return self.body[:limit]

            def shutdown(self, how):
                self.cancelled.set()

            def close(self):
                self.cancelled.set()

        return Connection()


def conta(c, nome="Bot A", secret=SECRET):
    response = c.post(
        "/contas-envio",
        json={
            "operacao_id": str(uuid4()),
            "nome": nome,
            "plataforma": "telegram",
            "provedor": "telegram_bot_api",
        },
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    response = c.put(
        f"/contas-envio/{identifier}/credencial",
        json={"operacao_id": str(uuid4()), "versao": 1, "credencial": secret},
    )
    assert response.status_code == 200, response.text
    return identifier


@pytest.fixture
def recepcao(cadastro, monkeypatch):
    c, app, token = cadastro
    runtime = app.state.telegram_recepcao
    assert runtime.parar()
    server = Servidor()
    monkeypatch.setattr("app.provedores.telegram.HTTPSConnection", server.connection)
    monkeypatch.setattr("app.runtime.telegram_recepcao.Telegram", Telegram)
    svc = app.state.consentimentos
    scope = svc.criar_escopo(EscopoEntrada(operacao_id=uuid4(), nome="Operação local"), token)
    account = conta(c)
    processed = Queue()
    original = svc.receber_telegram

    def receber(*args):
        result = original(*args)
        processed.put(args[2]["update_id"])
        return result

    monkeypatch.setattr(svc, "receber_telegram", receber)
    f = SimpleNamespace(
        c=c,
        app=app,
        token=token,
        runtime=runtime,
        server=server,
        svc=svc,
        account=account,
        scope=scope["id"],
        processed=processed,
        original=original,
    )
    yield f
    assert f.runtime.drenar()


def iniciar(f, updates):
    f.server.updates[1001] = updates
    f.runtime.iniciar(f.token)


def aguardar(f, count):
    return [f.processed.get(timeout=5) for _ in range(count)]


def contexto(f):
    with f.svc.repo.transacao() as s:
        a = s.get(ContaEnvio, f.account)
        a.telegram_bot_id = "1001"
        return a.id, a.segredo_ref, a.versao


def estado(f):
    with f.svc.repo.transacao() as s:
        return {
            "contatos": s.scalar(select(func.count()).select_from(Contato)),
            "identidades": s.scalar(select(func.count()).select_from(IdentidadeTelegram)),
            "eventos": s.scalar(select(func.count()).select_from(EventoTelegram)),
            "recibos": s.scalar(select(func.count()).select_from(ReciboTelegram)),
            "offset": s.get(ContaEnvio, f.account).telegram_proximo_offset,
            "inscricao": s.scalar(
                select(InscricaoConta.estado).where(InscricaoConta.conta_envio_id == f.account)
            ),
        }


def test_start_stop_ignoradas_e_offset_apos_commit(recepcao, caplog):
    f = recepcao
    iniciar(
        f,
        [
            update(1),
            update(2, private=False),
            update(3, "texto privado"),
            update(4, "/stop"),
            update(5),
            update(6, "/aceitar"),
        ],
    )
    assert aguardar(f, 6) == list(range(1, 7))
    f.server.wait(
        lambda: any(m == "getUpdates" and p.get("offset") == 7 for _, m, p in f.server.requests)
    )
    assert estado(f) == {
        "contatos": 1,
        "identidades": 1,
        "eventos": 3,
        "recibos": 6,
        "offset": 7,
        "inscricao": "revogada",
    }
    with f.svc.repo.transacao() as s:
        assert s.execute(text("SELECT count(*) FROM preferencias_contato")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM telefones_contato")).scalar() == 0
        assert (
            s.scalar(
                select(func.count())
                .select_from(EventoTelegram)
                .where(EventoTelegram.tipo == "autorizou")
            )
            == 0
        )
    for value in (SECRET, "texto privado", "PII_NAO_PERSISTIR"):
        assert value not in caplog.text


@pytest.mark.parametrize(
    "alteracao",
    [
        lambda u: u.update(edited_message=u.pop("message")),
        lambda u: u["message"].update(forward_origin={"type": "user"}),
        lambda u: u["message"].update(text="/start@outro_bot"),
        lambda u: u["message"]["from"].update(is_bot=True),
        lambda u: u["message"].update(text="/reativar"),
    ],
)
def test_atualizacoes_ignoradas_nao_criam_consentimento(recepcao, alteracao):
    f = recepcao
    data = update(1)
    alteracao(data)
    iniciar(f, [data])
    aguardar(f, 1)
    assert estado(f) == {
        "contatos": 0,
        "identidades": 0,
        "eventos": 0,
        "recibos": 1,
        "offset": 2,
        "inscricao": None,
    }


def test_replay_e_novo_receptor_usam_cursor_persistido(recepcao):
    f = recepcao
    iniciar(f, [update(1), update(2, "/stop")])
    aguardar(f, 2)
    assert f.runtime.drenar()
    f.runtime = RecepcaoTelegram(f.svc, f.app.state.contas_envio)
    f.server.replay = True  # Servidor controlado repete até atualizações já confirmadas.
    f.runtime.iniciar(f.token)
    aguardar(f, 2)
    assert estado(f)["recibos"] == 2 and estado(f)["eventos"] == 2
    assert estado(f)["offset"] == 3 and estado(f)["inscricao"] == "revogada"


@pytest.mark.parametrize("tipo", ["iniciou", "revogou"])
def test_falha_antes_commit_reverte_recibo_efeitos_e_cursor(recepcao, tipo):
    f = recepcao
    ctx, bot = contexto(f), {"id": 1001}
    if tipo == "revogou":
        f.original(ctx, bot, Telegram._normalizar(update(1), "bot_teste"), f.token, lambda: True)
    before = estado(f)
    data = Telegram._normalizar(update(2, "/stop" if tipo == "revogou" else "/start"), "bot_teste")

    def falhar(_conn, _cursor, sql, *_):
        if sql.startswith("INSERT INTO telegram_recibos"):
            raise RuntimeError("Falha controlada antes do commit")

    engine = f.svc.repo.database.engine
    event.listen(engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(RuntimeError):
            f.original(ctx, bot, data, f.token, lambda: True)
    finally:
        event.remove(engine, "after_cursor_execute", falhar)
    assert estado(f) == before
    f.original(ctx, bot, data, f.token, lambda: True)
    assert estado(f)["offset"] == 3
    assert estado(f)["recibos"] == before["recibos"] + 1


def test_erro_apos_commit_rele_cursor_sem_duplicar(recepcao, monkeypatch):
    f = recepcao
    delivered = Event()

    def incerto(*args):
        f.original(*args)
        delivered.set()
        raise RuntimeError("Resultado local incerto após commit")

    monkeypatch.setattr(f.svc, "receber_telegram", incerto)
    iniciar(f, [update(1)])
    assert delivered.wait(5)
    f.server.wait(
        lambda: any(m == "getUpdates" and p.get("offset") == 2 for _, m, p in f.server.requests)
    )
    assert estado(f)["eventos"] == estado(f)["recibos"] == 1


def test_duas_contas_bots_distintos_e_mesmo_update_id(recepcao):
    f = recepcao
    other = conta(f.c, "Bot B", "1002:SENTINELA_B")
    f.server.updates[1002] = [update(1, "/stop")]
    iniciar(f, [update(1)])
    aguardar(f, 2)
    with f.svc.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(IdentidadeTelegram)) == 1
        assert {i.conta_envio_id: i.estado for i in s.scalars(select(InscricaoConta))} == {
            f.account: "iniciada",
            other: "revogada",
        }
        assert s.scalar(select(func.count()).select_from(ReciboTelegram)) == 2


def test_mesmo_bot_em_duas_contas_tem_um_consumidor(recepcao):
    f = recepcao
    other_secret = "1002:SENTINELA_MESMO_BOT"
    conta(f.c, "Duplicada", other_secret)
    f.server.bots[other_secret] = 1001
    iniciar(f, [update(1)])
    aguardar(f, 1)
    f.server.wait(lambda: sum(m == "getMe" for _, m, _ in f.server.requests) == 2)
    assert f.runtime.parar()
    polled = {t for t, m, _ in f.server.requests if m == "getUpdates"}
    assert len(polled) == 1
    assert estado(f)["recibos"] == 1


def test_troca_credencial_descarta_resposta_antiga(recepcao):
    f = recepcao
    f.server.block_token = SECRET
    iniciar(f, [update(1)])
    assert f.server.em_rede.wait(5)
    new = "1001:SENTINELA_TELEGRAM_ROTACIONADA"
    with f.svc.repo.transacao() as s:
        version = s.get(ContaEnvio, f.account).versao
    assert (
        f.c.put(
            f"/contas-envio/{f.account}/credencial",
            json={"operacao_id": str(uuid4()), "versao": version, "credencial": new},
        ).status_code
        == 200
    )
    f.server.release.set()
    aguardar(f, 1)
    assert any(t == new and m == "getMe" for t, m, _ in f.server.requests)
    assert estado(f)["recibos"] == estado(f)["eventos"] == 1


def test_logout_interrompe_http_e_login_reinicia(recepcao):
    f = recepcao
    f.server.block_token = SECRET
    iniciar(f, [update(1)])
    assert f.server.em_rede.wait(5)
    f.app.state.auth.logout(f.token)
    assert not f.runtime._thread.is_alive()
    assert estado(f)["recibos"] == 0
    f.server.release.set()
    response = f.c.post(
        "/auth/login", json={"login": "teste", "senha": "frase fictícia de teste 489!"}
    )
    assert response.status_code == 200
    aguardar(f, 1)
    assert estado(f)["recibos"] == 1


@pytest.mark.parametrize("error", [TimeoutError, OSError])
def test_erros_de_rede_nao_expoem_token_nem_confirmam(recepcao, error, caplog):
    f = recepcao
    f.server.error = error
    iniciar(f, [update(1)])
    f.server.wait(lambda: len(f.server.requests) >= 1)
    assert f.runtime.parar()
    assert estado(f)["recibos"] == 0
    assert SECRET not in caplog.text and "corpo privado" not in caplog.text


def test_conflito_webhook_suspende_sem_remover_webhook(recepcao, caplog):
    f = recepcao
    f.server.status = 409
    iniciar(f, [update(1)])
    f.server.wait(lambda: any(m == "getUpdates" for _, m, _ in f.server.requests))
    assert f.runtime.parar()
    assert estado(f)["recibos"] == 0
    assert {m for _, m, _ in f.server.requests} <= {"getMe", "getUpdates"}
    assert SECRET not in caplog.text


def test_migracao_0012_preserva_bloco1_e_integridade(recepcao):
    f = recepcao
    from app.consentimentos.schemas import IdentidadeTelegramEntrada, TelegramEventoEntrada
    from app.contatos.repository import agora, uuid

    with f.svc.repo.transacao() as session:
        contact = Contato(id=uuid(), criado_em=agora(), atualizado_em=agora())
        session.add(contact)
        session.flush()
        contact_id = contact.id
    identity = f.svc.criar_identidade_telegram(
        contact_id,
        IdentidadeTelegramEntrada(
            operacao_id=uuid4(), usuario_externo_id="12345", chat_externo_id="12345"
        ),
        f.token,
    )
    for operation in (f.svc.inscrever_telegram, f.svc.revogar_telegram):
        operation(
            f.account,
            identity["id"],
            TelegramEventoEntrada(
                operacao_id=uuid4(),
                escopo_id=f.scope,
                bot_externo_id="1001",
                evento_externo_id=str(uuid4()),
                ocorrido_em=agora(),
            ),
            f.token,
        )
    db = f.svc.repo.database
    tables = ("identidades_telegram", "inscricoes_conta", "eventos_telegram")
    with db.transaction() as session:
        history = {name: session.execute(text("SELECT * FROM " + name)).all() for name in tables}
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.downgrade(config, "0011_telegram_optin")
        before = conn.exec_driver_sql("SELECT * FROM contas_envio").all()
        command.upgrade(config, "0012_telegram_recepcao")
        after = conn.exec_driver_sql("SELECT * FROM contas_envio").all()
        assert [row[:-1] for row in after] == [tuple(row) for row in before]
        assert all(row[-1] is None for row in after)
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.upgrade(config, "0018_telegram_executor_lote")
        schema_validator(config, "0018_telegram_executor_lote")(conn)
        assert {
            name: conn.exec_driver_sql("SELECT * FROM " + name).all() for name in tables
        } == history
    assert read_revision(f.app.state.persistence.settings.database_path) == (
        "0018_telegram_executor_lote"
    )
    ctx = contexto(f)
    f.original(
        ctx, {"id": 1001}, Telegram._normalizar(update(1), "bot_teste"), f.token, lambda: True
    )
    assert estado(f)["inscricao"] == "revogada"
    with pytest.raises(RuntimeError), bundled_migrations() as location:
        with db.engine.begin() as conn:
            config = migration_config(location)
            config.attributes["connection"] = conn
            command.downgrade(config, "0011_telegram_optin")
    with pytest.raises(IntegrityError), db.transaction() as s:
        s.execute(text("DELETE FROM telegram_recibos"))


def test_fechamento_real_drena_antes_banco(settings, authorization, monkeypatch):
    server = Servidor()
    server.block_token = SECRET
    monkeypatch.setattr("app.provedores.telegram.HTTPSConnection", server.connection)
    monkeypatch.setattr("app.runtime.telegram_recepcao.Telegram", Telegram)
    app = create_app(settings, authorization=authorization)
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {authorization.token}"}
    ) as c:
        assert app.state.telegram_recepcao._thread is None
        c.post(
            "/auth/configuracao-inicial",
            json={"nome": "Teste", "login": "teste", "senha": "frase fictícia de teste 489!"},
        )
        assert app.state.telegram_recepcao._thread is None
        token = c.post(
            "/auth/login", json={"login": "teste", "senha": "frase fictícia de teste 489!"}
        ).json()["token"]
        c.headers["X-eDisk-Session"] = token
        conta(c)
        assert server.em_rede.wait(5)
        runtime, db = app.state.telegram_recepcao, app.state.persistence.database
    assert not runtime._thread.is_alive()
    assert db.closed and app.state.shutdown_complete


def test_nenhum_ack_antes_commit(recepcao):
    f = recepcao
    inserted, release = Event(), Event()

    def pausar(_conn, _cursor, sql, *_):
        if sql.startswith("INSERT INTO telegram_recibos"):
            inserted.set()
            assert release.wait(5)

    engine = f.svc.repo.database.engine
    event.listen(engine, "after_cursor_execute", pausar)
    try:
        iniciar(f, [update(1)])
        assert inserted.wait(5)
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql("SELECT telegram_proximo_offset FROM contas_envio").scalar()
                is None
            )
            assert conn.exec_driver_sql("SELECT count(*) FROM telegram_recibos").scalar() == 0
        assert all("offset" not in p for _, m, p in f.server.requests if m == "getUpdates")
    finally:
        release.set()
        aguardar(f, 1)
        event.remove(engine, "after_cursor_execute", pausar)
    assert estado(f)["offset"] == 2


def test_sessao_expirada_durante_http_descarta_efeitos(recepcao, monkeypatch):
    from datetime import timedelta

    f = recepcao
    f.server.block_token = SECRET
    iniciar(f, [update(1)])
    assert f.server.em_rede.wait(5)
    now = f.app.state.auth.clock()
    monkeypatch.setattr(f.app.state.auth, "clock", lambda: now + timedelta(hours=9))
    f.server.release.set()
    assert f.runtime.parar()
    assert estado(f)["recibos"] == 0


def test_bot_diferente_apos_troca_credencial_nao_reutiliza_cursor(recepcao):
    f = recepcao
    iniciar(f, [update(1)])
    aguardar(f, 1)
    assert f.runtime.parar()
    with f.svc.repo.transacao() as s:
        version = s.get(ContaEnvio, f.account).versao
    assert (
        f.c.put(
            f"/contas-envio/{f.account}/credencial",
            json={
                "operacao_id": str(uuid4()),
                "versao": version,
                "credencial": "1002:SENTINELA_OUTRO_BOT",
            },
        ).status_code
        == 200
    )
    f.runtime.iniciar(f.token)
    f.server.wait(
        lambda: any(t.startswith("1002:") and m == "getMe" for t, m, _ in f.server.requests)
    )
    assert f.runtime.parar()
    assert not any(t.startswith("1002:") and m == "getUpdates" for t, m, _ in f.server.requests)
    assert estado(f)["offset"] == 2


def test_migracao_0012_falha_apos_ddl_reverte_cursor_e_tabela(recepcao):
    f = recepcao
    db = f.svc.repo.database
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.downgrade(config, "0011_telegram_optin")
    with db.engine.connect() as conn:
        before = conn.exec_driver_sql("SELECT * FROM sqlite_schema ORDER BY name").all()

    def falhar(_conn, _cursor, sql, *_):
        if sql.startswith("CREATE TABLE telegram_recibos"):
            raise RuntimeError("Falha controlada após DDL")

    event.listen(db.engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(RuntimeError), bundled_migrations() as location:
            with db.engine.begin() as conn:
                config = migration_config(location)
                config.attributes["connection"] = conn
                command.upgrade(config, "0012_telegram_recepcao")
    finally:
        event.remove(db.engine, "after_cursor_execute", falhar)
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT * FROM sqlite_schema ORDER BY name").all() == before
    assert read_revision(f.app.state.persistence.settings.database_path) == "0011_telegram_optin"
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.upgrade(config, "0012_telegram_recepcao")


def test_stop_start_e_reativacao_explicita_persistem(recepcao):
    f = recepcao
    iniciar(f, [update(1, "/stop"), update(2)])
    aguardar(f, 2)
    assert f.runtime.parar()
    assert estado(f)["inscricao"] == "revogada"
    assert estado(f)["recibos"] == estado(f)["eventos"] == 2
    iniciar(f, [update(1, "/stop"), update(2), update(3, "/reativar")])
    aguardar(f, 1)
    assert f.runtime.parar()
    assert estado(f)["inscricao"] == "revogada"
    assert estado(f)["recibos"] == estado(f)["eventos"] == 3
    iniciar(
        f,
        [
            update(1, "/stop"),
            update(2),
            update(3, "/reativar"),
            update(4, "/aceitar promocoes"),
        ],
    )
    aguardar(f, 1)
    assert estado(f)["inscricao"] == "iniciada"
    assert estado(f)["recibos"] == estado(f)["eventos"] == 4
    with f.svc.repo.transacao() as s:
        assert s.scalar(
            select(func.count())
            .select_from(EventoTelegram)
            .where(EventoTelegram.tipo == "revogou")
        ) == 1
        assert s.scalar(
            select(func.count())
            .select_from(EventoTelegram)
            .where(EventoTelegram.tipo == "autorizou")
        ) == 1


def test_cursor_reinicia_por_id_aleatorio_apos_semana_sem_updates(recepcao, monkeypatch):
    from datetime import timedelta

    f = recepcao
    iniciar(f, [update(1000)])
    aguardar(f, 1)
    assert f.runtime.parar()
    later = agora() + timedelta(days=8)
    monkeypatch.setattr("app.runtime.telegram_recepcao.agora", lambda: later)
    monkeypatch.setattr("app.consentimentos.service.agora", lambda: later)
    iniciar(f, [update(1, "/stop")])
    aguardar(f, 1)
    assert estado(f)["offset"] == 2
    assert estado(f)["recibos"] == 2 and estado(f)["inscricao"] == "revogada"
