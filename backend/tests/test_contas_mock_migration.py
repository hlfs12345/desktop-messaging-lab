"""0007 populada: upgrade localizado, autenticacao real e rollback de DDL."""

import sqlite3
from contextlib import closing

import pytest
import test_campanhas as campanhas
import test_contas_envio as contas
import test_preparacoes as prep
from alembic import command
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.persistencia.errors import MigrationError
from app.persistencia.migrations import (
    bundled_migrations,
    migrate,
    migration_config,
    read_revision,
)

cadastro = prep.cadastro
consentimento = prep.consentimento
rascunho = prep.rascunho
pronta = prep.pronta
HEAD = "0012_telegram_recepcao"
ORIGEM = "0007_preparacoes_cifradas"


def revision(db, target):
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        if target == HEAD:
            command.upgrade(config, target)
        else:
            command.downgrade(config, target)


def upgrade(app):
    settings = app.state.persistence.settings
    with bundled_migrations() as location:
        return migrate(
            app.state.persistence.database.engine,
            settings.database_path,
            settings.snapshots_dir,
            ORIGEM,
            migration_config(location),
            existing=True,
        )


def snapshot(conn):
    names = (
        conn.exec_driver_sql(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name!='alembic_version' ORDER BY name"
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


def objects(conn):
    rows = conn.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    ).all()
    return [
        (kind, name, table, " ".join(sql.split()).replace(" )", ")"))
        if kind == "table" and name == "contas_envio"
        else (kind, name, table, sql)
        for kind, name, table, sql in rows
    ]


def sem_telegram(dados, objetos):
    """Comparar o recorte histórico sem perder a verificação dos dados adicionados."""
    tables = {"identidades_telegram", "inscricoes_conta", "eventos_telegram"}
    if "telegram_recibos" in dados:
        tables.add("telegram_recibos")
        assert all(row[-1] is None for row in dados["contas_envio"])
        dados["contas_envio"] = [row[:-1] for row in dados["contas_envio"]]
    for table in tables:
        assert dados.pop(table) == []
    assert all(row[-1] is None for row in dados["contas_envio"])
    dados["contas_envio"] = [row[:-1] for row in dados["contas_envio"]]
    result = []
    for kind, name, table, sql in objetos:
        if table in tables or name in {"uq_contas_telegram_bot", "contas_bot_preservado"}:
            continue
        if kind == "table" and name == "contas_envio":
            start = sql.index(", telegram_bot_id TEXT CHECK")
            end = sql.index(", CONSTRAINT", start)
            sql = sql[:start] + sql[end:]
        result.append((kind, name, table, sql))
    return dados, result


@pytest.fixture
def populada_0007(pronta):
    r, identifier, _ = pronta
    c, app, _, _, identity, *_ = r
    for platform, provider in (("whatsapp", "meta_cloud"), ("telegram", "telegram_bot_api")):
        account = contas.criar(c, plataforma=platform, provedor=provider)
        saved = c.put(
            "/contas-envio/" + account["id"] + "/credencial",
            json=prep.op(versao=1, credencial="credencial-real-ficticia"),
        )
        assert saved.status_code == 200, saved.text
    first, _ = prep.preparar(pronta)
    root = "/campanhas/" + identifier
    detail = c.get(root + "/preparacoes/" + first["preparacao_id"]).json()
    confirmed = c.post(
        root + "/preparacoes/" + first["preparacao_id"] + "/confirmacao",
        json=prep.op(
            versao=first["versao"],
            preparacao_id=first["preparacao_id"],
            impressao=detail["impressao"],
        ),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert (
        c.post(
            root + "/retorno-rascunho",
            json=prep.op(
                versao=confirmed.json()["versao"],
                preparacao_id=first["preparacao_id"],
            ),
        ).status_code
        == 200
    )
    second, _ = prep.preparar(pronta)
    detail = c.get(root + "/preparacoes/" + second["preparacao_id"]).json()
    assert (
        c.post(
            root + "/preparacoes/" + second["preparacao_id"] + "/confirmacao",
            json=prep.op(
                versao=second["versao"],
                preparacao_id=second["preparacao_id"],
                impressao=detail["impressao"],
            ),
        ).status_code
        == 200
    )
    draft = campanhas.criar(r, operacao_id=str(prep.uuid4()), nome="Rascunho preservado")
    assert c.get("/campanhas/" + draft["id"]).json()["status"] == "rascunho"
    revision(app.state.persistence.database, ORIGEM)
    return c, app, root, identity, (first["preparacao_id"], second["preparacao_id"])


def test_upgrade_0007_preserva_fotografias_e_snapshot(populada_0007):
    c, app, root, identity, ids = populada_0007
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before = snapshot(conn)
        schema = objects(conn)
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_preparacao_itens WHERE conteudo_cifrado IS NOT NULL"
            ).scalar_one()
            == 2
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_preparacoes WHERE confirmado_em IS NOT NULL"
            ).scalar_one()
            == 2
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_preparacoes WHERE invalidado_em IS NOT NULL"
            ).scalar_one()
            == 1
        )
    saved = upgrade(app)
    assert read_revision(saved) == ORIGEM
    with closing(sqlite3.connect(saved)) as archived:
        for name, rows in before.items():
            assert (
                sorted(archived.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
                == rows
            )
    assert read_revision(app.state.persistence.settings.database_path) == HEAD
    with db.engine.begin() as conn:
        after, schema_after = sem_telegram(snapshot(conn), objects(conn))
        assert after.pop("campanha_autorizacoes_simulacao") == []
        operacionais = {
            "campanha_execucoes",
            "campanha_destinatarios",
            "campanha_execucao_itens",
            "tentativas_envio",
            "campanha_execucao_transicoes",
            "consentimento_suspensoes",
        }
        for tabela in operacionais:
            assert after.pop(tabela) == []
        assert after == before  # Inclui UUIDs, todos os BLOBs, manifestos e auditoria.
        # 0008 altera contas; 0009 adiciona autorizacoes, sem reescrever objetos anteriores.
        assert [
            r
            for r in schema_after
            if r[1] not in ("contas_envio", "preparacao_autorizada_sem_invalidacao")
            and r[2] != "campanha_autorizacoes_simulacao"
            and r[2] not in operacionais
            and r[1]
            not in {
                "preparacao_operacional_preservada_0010",
                "campanha_operacional_preservada_0010",
            }
        ] == [r for r in schema if r[1] != "contas_envio"]
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA defer_foreign_keys").scalar_one() == 0
    for pid in ids:
        response = c.get(root + "/preparacoes/" + pid)
        assert response.status_code == 200, response.text
        assert response.json()["integridade"] == "verificada"
        assert response.json()["confirmado_em"]
        content = c.get(root + "/preparacoes/" + pid + "/itens/" + identity["id"] + "/conteudo")
        assert content.status_code == 200, content.text
    revision(db, ORIGEM)
    with db.engine.begin() as conn:
        assert snapshot(conn) == before
        assert objects(conn) == schema
    revision(db, HEAD)


@pytest.mark.parametrize(
    "stage",
    [
        "CREATE TEMP TABLE contas_copia_0008",
        "DROP TABLE contas_envio",
        "INSERT INTO contas_envio SELECT",
        "CREATE TRIGGER contas_envio_sem_delete",
        "UPDATE alembic_version",
    ],
)
def test_rollback_0008_apos_escrita_e_ddl(populada_0007, stage):
    c, app, root, _, ids = populada_0007
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)

    def fail(conn, cursor, sql, *_):
        if sql.startswith(stage):
            raise RuntimeError("Falha injetada apos escrita")

    event.listen(db.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(MigrationError):
            upgrade(app)
    finally:
        event.remove(db.engine, "after_cursor_execute", fail)
    assert read_revision(app.state.persistence.settings.database_path) == ORIGEM
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA defer_foreign_keys").scalar_one() == 0
    assert c.get(root + "/preparacoes/" + ids[0]).json()["integridade"] == "verificada"
    # Nova tentativa funciona; nao restam tabelas temporarias nem revisao parcialmente aplicada.
    upgrade(app)


def test_commit_nao_ignora_fk_invalida(populada_0007):
    _, app, *_ = populada_0007
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        with bundled_migrations() as location:
            config = migration_config(location)
            config.attributes["connection"] = conn
            command.upgrade(config, HEAD)
        conn.exec_driver_sql("DROP TRIGGER contas_envio_identidade_fixa")
        conn.exec_driver_sql(
            "UPDATE contas_envio SET criado_por_usuario_id='00000000-0000-0000-0000-000000000000'"
        )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all()
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
    assert read_revision(app.state.persistence.settings.database_path) == ORIGEM


@pytest.mark.parametrize("removed", [False, True])
def test_downgrade_recusa_mock_inclusive_removida(cadastro, removed):
    c, app, _ = cadastro
    item = contas.criar(c, plataforma="whatsapp", provedor="mock")
    if removed:
        assert (
            c.patch("/contas-envio/" + item["id"], json=prep.op(versao=1, remover=True)).status_code
            == 200
        )
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        revision(db, ORIGEM)
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
    assert read_revision(app.state.persistence.settings.database_path) == HEAD
