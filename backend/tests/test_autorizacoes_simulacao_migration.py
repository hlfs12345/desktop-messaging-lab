"""0008 povoada e SQLite real: decisoes, DDL transacional e fotografias CNG."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import test_contas_envio as contas
import test_contas_mock_migration as historico
import test_preparacoes_simulacao as simulacao
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from app.campanhas.models import AutorizacaoSimulacao
from app.contatos.repository import agora
from app.persistencia.errors import MigrationError
from app.persistencia.migrations import bundled_migrations, migrate, migration_config, read_revision
from app.persistencia.models import Base, utc_text

cadastro = simulacao.cadastro
consentimento = simulacao.consentimento
rascunho = simulacao.rascunho
pronta = simulacao.pronta
simulavel = simulacao.simulavel

TABLE = "campanha_autorizacoes_simulacao"
ORIGEM = "0008_contas_mock"
HEAD = "0009_autorizacoes_simulacao"
OPERACIONAIS = {
    "campanha_execucoes",
    "campanha_destinatarios",
    "campanha_execucao_itens",
    "tentativas_envio",
    "campanha_execucao_transicoes",
    "consentimento_suspensoes",
}
GATILHOS_OPERACIONAIS = {
    "preparacao_operacional_preservada_0010",
    "campanha_operacional_preservada_0010",
}


def revision(db, target, *, downgrade=False):
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        (command.downgrade if downgrade else command.upgrade)(config, target)


def dados(conn, **changes):
    row = (
        conn.exec_driver_sql(
            "SELECT p.id,p.campanha_id,p.base_id,p.impressao,p.fotografia_json,"
            "p.confirmado_por_usuario_id FROM campanha_preparacoes p "
            "WHERE p.invalidado_em IS NULL AND p.confirmado_em IS NOT NULL"
        )
        .mappings()
        .one()
    )
    return {
        "id": str(uuid4()),
        "campanha_id": row["campanha_id"],
        "preparacao_id": row["id"],
        "base_id": row["base_id"],
        "impressao": row["impressao"],
        "resumo_configuracao": json.loads(row["fotografia_json"])["configuracao_simulacao"][
            "resumo_configuracao"
        ],
        "autorizado_por_usuario_id": row["confirmado_por_usuario_id"],
        "autorizado_em": utc_text(agora()),
        "estado": "vigente",
        "versao": 1,
        "revogado_por_usuario_id": None,
        "revogado_em": None,
        "motivo_codigo": None,
        **changes,
    }


def inserir(conn, value, *, replace=False):
    # Nomes fixos do teste; valores sempre parametrizados.
    names = list(value)
    sql = (
        "INSERT "
        + ("OR REPLACE " if replace else "")
        + "INTO "
        + TABLE
        + " ("
        + ",".join(names)
        + ") VALUES ("
        + ",".join("?" for _ in names)
        + ")"
    )
    conn.exec_driver_sql(sql, tuple(value[name] for name in names))


def revogar(conn, value, *, versao=2, usuario=None, data=None, motivo="revogacao_manual"):
    return conn.exec_driver_sql(
        "UPDATE " + TABLE + " SET estado='revogada',versao=?,revogado_por_usuario_id=?,"
        "revogado_em=?,motivo_codigo=? WHERE id=?",
        (
            versao,
            usuario or value["autorizado_por_usuario_id"],
            data or utc_text(agora()),
            motivo,
            value["id"],
        ),
    )


def snapshot(conn):
    return historico.snapshot(conn)


def objects(conn):
    return historico.objects(conn)


@pytest.fixture
def populada_0008(simulavel):
    r, identifier, _ = simulavel
    c, app, _, _, identity, *_ = r
    real = contas.criar(c, plataforma="whatsapp", provedor="meta_cloud")
    saved = c.put(
        "/contas-envio/" + real["id"] + "/credencial",
        json=simulacao.prep.op(versao=1, credencial="credencial-ficticia-preservada"),
    )
    assert saved.status_code == 200, saved.text
    first, _ = simulacao.preparar(simulavel)
    confirmed = simulacao.confirmar(simulavel, first)
    root = "/campanhas/" + identifier
    returned = c.post(
        root + "/retorno-rascunho",
        json=simulacao.prep.op(versao=confirmed["versao"], preparacao_id=first["preparacao_id"]),
    )
    assert returned.status_code == 200, returned.text
    second, _ = simulacao.preparar(simulavel)
    simulacao.confirmar(simulavel, second)
    db = app.state.persistence.database
    revision(db, ORIGEM, downgrade=True)
    return c, app, root, identity, (first["preparacao_id"], second["preparacao_id"])


@pytest.fixture
def persistida(populada_0008):
    c, app, root, identity, ids = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        value = dados(conn)
        inserir(conn, value)
    return db, value, populada_0008


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


def test_upgrade_preserva_dados_blobs_fotografias_e_confirmacao(populada_0008):
    c, app, root, identity, ids = populada_0008
    db = app.state.persistence.database
    before_texts = {
        pid: c.get(root + "/preparacoes/" + pid + "/itens/" + identity["id"] + "/conteudo").json()
        for pid in ids
    }
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)
        assert TABLE not in before
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_preparacao_itens WHERE conteudo_cifrado IS NOT NULL"
            ).scalar_one()
            == 2
        )
        assert {r[0] for r in conn.exec_driver_sql("SELECT provedor FROM contas_envio")} >= {
            "mock",
            "meta_cloud",
        }
    saved = upgrade(app)
    assert read_revision(saved) == ORIGEM
    with closing(sqlite3.connect(saved)) as archived:
        for name, rows in before.items():
            assert (
                sorted(archived.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
                == rows
            )
    with db.engine.begin() as conn:
        after, schema_after = historico.sem_telegram(snapshot(conn), objects(conn))
        assert after.pop(TABLE) == []  # Confirmacoes anteriores nao geram autorizacao.
        for tabela in OPERACIONAIS:
            assert after.pop(tabela) == []
        assert after == before  # Inclui todos os BLOBs, manifestos e auditoria.
        assert [
            r
            for r in schema_after
            if r[2] != TABLE
            and r[1] != "preparacao_autorizada_sem_invalidacao"
            and r[2] not in OPERACIONAIS
            and r[1] not in GATILHOS_OPERACIONAIS
        ] == schema
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    assert read_revision(app.state.persistence.settings.database_path) == "0012_telegram_recepcao"
    for pid in ids:
        detail = c.get(root + "/preparacoes/" + pid)
        assert detail.status_code == 200, detail.text
        assert detail.json()["integridade"] == "verificada"
        assert detail.json()["confirmado_em"]
        assert (
            c.get(root + "/preparacoes/" + pid + "/itens/" + identity["id"] + "/conteudo").json()
            == before_texts[pid]
        )
    revision(db, ORIGEM, downgrade=True)
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
    revision(db, HEAD)


def test_estrutura_indices_fks_e_utc_orm(persistida):
    db, value, _ = persistida
    inspector = inspect(db.engine)
    assert {c["name"] for c in inspector.get_columns(TABLE)} == set(value)
    assert len(inspector.get_foreign_keys(TABLE)) == 5
    assert all(fk["options"]["ondelete"] == "RESTRICT" for fk in inspector.get_foreign_keys(TABLE))
    assert inspector.get_unique_constraints(TABLE) == [
        {
            "name": "uq_campanha_autorizacoes_simulacao_preparacao_id",
            "column_names": ["preparacao_id"],
        }
    ]
    indexes = {i["name"]: i for i in inspector.get_indexes(TABLE)}
    assert indexes["ix_autorizacoes_simulacao_vigente"]["unique"]
    assert str(indexes["ix_autorizacoes_simulacao_vigente"]["dialect_options"]["sqlite_where"]) == (
        "estado='vigente'"
    )
    assert indexes["ix_autorizacoes_simulacao_historico"]["column_names"] == [
        "campanha_id",
        "autorizado_em",
        "id",
    ]
    with db.transaction() as s:
        stored = s.scalar(select(AutorizacaoSimulacao))
        assert stored.autorizado_em == datetime.fromisoformat(value["autorizado_em"])
        assert stored.autorizado_em.utcoffset().total_seconds() == 0
    with pytest.raises(StatementError, match="fuso horario|fuso hor"):
        with db.transaction() as s:
            obj = s.get(AutorizacaoSimulacao, value["id"])
            obj.autorizado_em = datetime(2026, 11, 2)
            s.flush()


@pytest.mark.parametrize(
    "key",
    [
        "id",
        "campanha_id",
        "preparacao_id",
        "base_id",
        "impressao",
        "resumo_configuracao",
        "autorizado_por_usuario_id",
        "autorizado_em",
    ],
)
def test_decisao_imutavel_inclusive_junto_com_revogacao(persistida, key):
    db, value, _ = persistida
    altered = (
        utc_text(agora())
        if key == "autorizado_em"
        else ("a" * 64 if key in {"impressao", "resumo_configuracao"} else str(uuid4()))
    )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE " + TABLE + " SET " + key + "=?,estado='revogada',versao=2,"
            "revogado_por_usuario_id=?,revogado_em=?,motivo_codigo='revogacao_manual' WHERE id=?",
            (altered, value["autorizado_por_usuario_id"], utc_text(agora()), value["id"]),
        )
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT " + key + " FROM " + TABLE).scalar_one() == value[key]


@pytest.mark.parametrize(
    "change",
    [
        {"estado": "executando"},
        {"estado": "revogada"},
        {"versao": 0},
        {"versao": 2},
        {"versao": 1.5},
        {"id": "curto"},
        {"impressao": "g" * 64},
        {"impressao": "0" * 64},
        {"resumo_configuracao": "X" * 64},
        {"resumo_configuracao": "a" * 63},
        {"autorizado_em": "2026-11-02T12:00:00+00:00"},
        {"autorizado_em": "2026-11-02T12:00:00.000000"},
        {"autorizado_em": "2026-99-99T12:00:00.000000Z"},
        {"autorizado_em": "2000-01-01T00:00:00.000000Z"},
        {"revogado_em": "2026-11-02T12:00:00.000000Z"},
        {"motivo_codigo": "telefone ou credencial"},
        {"revogado_por_usuario_id": str(uuid4())},
        {"autorizado_por_usuario_id": str(uuid4())},
        {"campanha_id": str(uuid4())},
        {"preparacao_id": str(uuid4())},
        {"base_id": str(uuid4())},
    ],
)
def test_insercao_invalida_sem_efeitos(populada_0008, change):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        inserir(conn, dados(conn, **change))
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM " + TABLE).scalar_one() == 0


@pytest.mark.parametrize("reason", ["revogacao_manual", "retorno_rascunho"])
def test_revogacao_terminal_versao_e_cas(persistida, reason):
    db, value, _ = persistida
    with db.engine.begin() as conn:
        assert revogar(conn, value, motivo=reason).rowcount == 1
        assert (
            conn.exec_driver_sql(
                "UPDATE " + TABLE + " SET versao=2 WHERE id=? AND versao=1", (value["id"],)
            ).rowcount
            == 0
        )  # CAS antigo nao afeta a decisao revogada.
    for sql in (
        "SET estado='vigente',versao=3,revogado_em=NULL,"
        "revogado_por_usuario_id=NULL,motivo_codigo=NULL",
        "SET versao=3",
        "SET motivo_codigo=motivo_codigo",
    ):
        with pytest.raises(IntegrityError), db.engine.begin() as conn:
            conn.exec_driver_sql("UPDATE " + TABLE + " " + sql + " WHERE id=?", (value["id"],))
    with db.engine.begin() as conn:
        stored = conn.exec_driver_sql("SELECT estado,versao,motivo_codigo FROM " + TABLE).one()
        assert tuple(stored) == ("revogada", 2, reason)


@pytest.mark.parametrize(
    "changes",
    [
        {"versao": 1},
        {"versao": 3},
        {"versao": 2.5},
        {"motivo": None},
        {"motivo": "texto livre"},
        {"usuario": str(uuid4())},
        {"data": "2000-01-01T00:00:00.000000Z"},
        {"data": "2026-11-02T12:00:00.000000+00:00"},
    ],
)
def test_revogacao_incoerente_rejeitada(persistida, changes):
    db, value, _ = persistida
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        revogar(conn, value, **changes)
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT estado,versao FROM " + TABLE).one() == ("vigente", 1)


@pytest.mark.parametrize("revoked", [False, True])
def test_delete_e_replace_por_id_ou_preparacao_rejeitados(persistida, revoked):
    db, value, _ = persistida
    with db.engine.begin() as conn:
        if revoked:
            revogar(conn, value)
        before = snapshot(conn)
    for replacement in (None, value, {**value, "id": str(uuid4())}):
        with pytest.raises(IntegrityError), db.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA recursive_triggers=OFF")
            if replacement is None:
                conn.exec_driver_sql("DELETE FROM " + TABLE)
            else:
                inserir(conn, replacement, replace=True)
        with db.engine.begin() as conn:
            assert snapshot(conn) == before


def test_indices_unicos_independentes_dos_triggers_e_replace_por_campanha(persistida):
    db, value, context = persistida
    old_id = context[4][0]
    # Remocao apenas em transacao descartavel que sera revertida: isola cada barreira.
    for same_prep in (False, True):
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            with db.engine.begin() as conn:
                conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_origem")
                conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_sem_replace")
                inserir(
                    conn,
                    {
                        **value,
                        "id": str(uuid4()),
                        "preparacao_id": value["preparacao_id"] if same_prep else old_id,
                        "estado": "revogada" if same_prep else "vigente",
                        "revogado_por_usuario_id": value["autorizado_por_usuario_id"]
                        if same_prep
                        else None,
                        "revogado_em": value["autorizado_em"] if same_prep else None,
                        "motivo_codigo": "revogacao_manual" if same_prep else None,
                    },
                )
    with pytest.raises(IntegrityError, match="Autorizacao existente"), db.engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_origem")
        inserir(conn, {**value, "id": str(uuid4()), "preparacao_id": old_id}, replace=True)
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT id FROM " + TABLE).scalar_one() == value["id"]


def test_origem_exige_vinculo_confirmado_e_preparacao_nao_invalidada(populada_0008, simulavel):
    c, app, root, _, ids = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        value = dados(conn)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        old = conn.exec_driver_sql(
            "SELECT impressao FROM campanha_preparacoes WHERE id=?", (ids[0],)
        ).scalar_one()
        inserir(conn, {**value, "preparacao_id": ids[0], "impressao": old})
    # As guardas acima foram exercitadas na 0009. A API atual exige o upgrade
    # que ocorre no startup antes de acessar as tabelas operacionais.
    revision(db, "0010_autorizacoes_utc")
    receipt = c.get(root).json()
    returned = c.post(
        root + "/retorno-rascunho",
        json=simulacao.prep.op(versao=receipt["versao"], preparacao_id=ids[1]),
    )
    assert returned.status_code == 200
    other = simulacao.prep.campanhas.criar(simulavel[0], operacao_id=str(uuid4()))
    assert other["id"] != value["campanha_id"]
    new, _ = simulacao.preparar(simulavel)
    photo = simulacao.fotografia(simulavel, new)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        inserir(
            conn,
            {
                **value,
                "preparacao_id": new["preparacao_id"],
                "impressao": photo["impressao"],
                "autorizado_em": utc_text(agora()),
            },
        )
    simulacao.confirmar(simulavel, new)
    with db.engine.begin() as conn:
        valid = dados(conn)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        inserir(conn, {**valid, "campanha_id": other["id"]})
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM " + TABLE).scalar_one() == 0


def test_revogacao_necessaria_antes_de_invalidar_preserva_historico(persistida):
    db, value, _ = persistida
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?",
            (utc_text(agora()), value["preparacao_id"]),
        )
    with db.engine.begin() as conn:
        revogar(conn, value, motivo="retorno_rascunho")
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?",
            (utc_text(agora()), value["preparacao_id"]),
        )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


@pytest.mark.parametrize("revoked", [False, True])
def test_downgrade_com_decisoes_recusado_sem_perda(persistida, revoked):
    db, value, context = persistida
    with db.engine.begin() as conn:
        if revoked:
            revogar(conn, value)
        before, schema = snapshot(conn), objects(conn)
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        revision(db, ORIGEM, downgrade=True)
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert read_revision(context[1].state.persistence.settings.database_path) == HEAD


@pytest.mark.parametrize(
    "stage",
    [
        "CREATE TABLE campanha_autorizacoes_simulacao",
        "CREATE UNIQUE INDEX ix_autorizacoes_simulacao_vigente",
        "CREATE TRIGGER autorizacoes_simulacao_transicao",
        "UPDATE alembic_version",
    ],
)
def test_rollback_ddl_e_revisao_apos_falha(populada_0008, stage):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)

    def fail(conn, cursor, sql, *_):
        if sql.startswith(stage):
            raise RuntimeError("Falha injetada apos DDL/escrita")

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
    upgrade(app)


@pytest.mark.parametrize("revoke", [False, True])
def test_rollback_decisao_apos_escrita_antes_da_auditoria(populada_0008, revoke):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        value = dados(conn)
        if revoke:
            inserir(conn, value)
        before = snapshot(conn)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        if revoke:
            revogar(conn, value)
        else:
            inserir(conn, value)
        assert conn.exec_driver_sql(
            "SELECT estado FROM " + TABLE + " WHERE id=?", (value["id"],)
        ).scalar_one() == ("revogada" if revoke else "vigente")
        # Falha real de FK APOS a escrita da decisao, sem implementar o servico B.
        conn.exec_driver_sql(
            "INSERT INTO auditoria VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                str(uuid4()),
                "teste",
                "autorizacao_simulacao",
                value["id"],
                "{}",
                utc_text(agora()),
            ),
        )
    with db.engine.begin() as conn:
        assert snapshot(conn) == before


def test_nova_preparacao_apos_revogacao_preserva_duas_decisoes(persistida, simulavel):
    db, value, context = persistida
    c, _, root, *_ = context
    with db.engine.begin() as conn:
        revogar(conn, value, motivo="retorno_rascunho")
    # Preservar a decisao legada ao atualizar para a revisao exigida pela API atual.
    revision(db, "0010_autorizacoes_utc")
    returned = c.post(
        root + "/retorno-rascunho",
        json=simulacao.prep.op(
            versao=c.get(root).json()["versao"], preparacao_id=value["preparacao_id"]
        ),
    )
    assert returned.status_code == 200, returned.text
    new, _ = simulacao.preparar(simulavel)
    simulacao.confirmar(simulavel, new)
    with db.engine.begin() as conn:
        inserir(conn, dados(conn))
        assert conn.exec_driver_sql("SELECT count(*) FROM " + TABLE).scalar_one() == 2
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM " + TABLE + " WHERE estado='vigente'"
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("field", ["revogado_por_usuario_id", "revogado_em", "motivo_codigo"])
def test_revogacao_exige_todos_os_metadados(persistida, field):
    db, value, _ = persistida
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE " + TABLE + " SET estado='revogada',versao=2,"
            "revogado_por_usuario_id=?,revogado_em=?,motivo_codigo='revogacao_manual',"
            + field
            + "=NULL WHERE id=?",
            (value["autorizado_por_usuario_id"], utc_text(agora()), value["id"]),
        )
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT estado,versao FROM " + TABLE).one() == ("vigente", 1)


def test_downgrade_vazio_com_falha_apos_drop_reverte_ddl(populada_0008):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        before, schema = snapshot(conn), objects(conn)

    def fail(conn, cursor, sql, *_):
        if sql.lstrip().startswith("DROP TABLE campanha_autorizacoes_simulacao"):
            raise RuntimeError("Falha injetada apos DROP TABLE")

    event.listen(db.engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Falha injetada"):
            revision(db, ORIGEM, downgrade=True)
    finally:
        event.remove(db.engine, "after_cursor_execute", fail)
    assert read_revision(app.state.persistence.settings.database_path) == HEAD
    with db.engine.begin() as conn:
        assert snapshot(conn) == before and objects(conn) == schema
    revision(db, ORIGEM, downgrade=True)


DATAS_INVALIDAS = [
    "2026-11-03T12:00:00.123456Z\x00",
    "2026-11-03T12:00:00.123456Z\x00extra",
    "2026-11-03T12:00:00.12345\x00Z",
    "2026-11-31T12:00:00.000000Z",
    "2026-02-29T12:00:00.000000Z",
    "2026-04-31T12:00:00.000000Z",
    "1900-02-29T12:00:00.000000Z",
    "2100-02-29T12:00:00.000000Z",
    "0000-01-01T00:00:00.000000Z",
    "2026-13-01T12:00:00.000000Z",
    "2026-01-00T12:00:00.000000Z",
    "2026-11-03T24:00:00.000000Z",
    "2026-11-03T12:60:00.000000Z",
    "2026-11-03T12:00:60.000000Z",
    "2026-11-03T12:00:00.123456",
    "2026-11-03T12:00:00.123456+00:00",
    "2026-11-03T12:00:00.123456-03:00",
    "2026-11-03 12:00:00.123456Z",
    "2026-11-03T12:00:00.123Z",
    "2026-11-03T12:00:00.1234567Z",
]
DATAS_VALIDAS = [
    "0001-01-01T00:00:00.000000Z",
    "0999-12-31T23:59:59.000001Z",
    "2000-02-29T12:00:00.123456Z",
    "2024-02-29T12:00:00.000000Z",
    "2024-02-29T23:59:59.999999Z",
    "2026-11-30T23:59:59.999999Z",
    "2026-11-03T12:00:00.000001Z",
    "2026-11-03T12:00:00.123456Z",
    "9999-12-31T23:59:59.999999Z",
]


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
@pytest.mark.parametrize("valor", DATAS_VALIDAS)
def test_datas_canonicas_preservam_microssegundos_no_orm(populada_0008, campo, valor):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        # Apenas nesta base descartavel: isolar CHECK de calendario da cronologia
        # da preparacao real, criada em 2026, para testar anos anteriores/bissextos.
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_origem")
        value = dados(
            conn, autorizado_em=valor if campo == "autorizado_em" else "0001-01-01T00:00:00.000000Z"
        )
        inserir(conn, value)
        if campo == "revogado_em":
            revogar(conn, value, data=valor)
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with db.transaction() as session:
        item = session.get(AutorizacaoSimulacao, value["id"])
        expected = datetime.fromisoformat(valor)
        assert getattr(item, campo) == expected
        assert utc_text(getattr(item, campo)) == valor
        assert getattr(item, campo).microsecond == int(valor[20:26])


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
@pytest.mark.parametrize("valor", DATAS_INVALIDAS)
def test_sql_rejeita_datas_invalidas_e_reverte_escrita_anterior(populada_0008, campo, valor):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_origem")
        value = dados(conn, autorizado_em="0001-01-01T00:00:00.000000Z")
        if campo == "revogado_em":
            inserir(conn, value)
        before = snapshot(conn)
    with pytest.raises(IntegrityError, match="ck_campanha_autorizacoes_simulacao_datas"):
        with db.engine.begin() as conn:
            audit_id = str(uuid4())
            conn.exec_driver_sql(
                "INSERT INTO auditoria VALUES (?,?,?,?,?,?,?)",
                (
                    audit_id,
                    value["autorizado_por_usuario_id"],
                    "teste_datas",
                    "autorizacao_simulacao",
                    value["id"],
                    "{}",
                    utc_text(agora()),
                ),
            )
            assert (
                conn.exec_driver_sql(
                    "SELECT id FROM auditoria WHERE id=?", (audit_id,)
                ).scalar_one()
                == audit_id
            )
            if campo == "autorizado_em":
                inserir(conn, {**value, "autorizado_em": valor})
            else:
                revogar(conn, value, data=valor)
    with db.engine.begin() as conn:
        assert snapshot(conn) == before
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_check_datas_modelo_e_migracao_identicos(populada_0008):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    revision(db, HEAD)
    name = "ck_campanha_autorizacoes_simulacao_datas"
    actual = next(
        item["sqltext"]
        for item in inspect(db.engine).get_check_constraints(TABLE)
        if item["name"] == name
    )
    expected = next(
        str(item.sqltext)
        for item in AutorizacaoSimulacao.__table__.constraints
        if item.name == name
    )
    assert " ".join(actual.split()) == " ".join(expected.split())


def test_calendario_sql_equivale_ao_python_em_ciclo_gregoriano_completo():
    expression = next(
        str(item.sqltext)
        for item in AutorizacaoSimulacao.__table__.constraints
        if item.name == "ck_campanha_autorizacoes_simulacao_datas"
    )
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute(
            "CREATE TABLE datas (autorizado_em TEXT NOT NULL, revogado_em TEXT, CHECK("
            + expression
            + "))"
        )
        # Ciclo completo de 400 anos, incluindo 1900/2000/2100/2200/2299.
        for year in range(1900, 2300):
            for month in range(1, 13):
                for day in (28, 29, 30, 31):
                    value = f"{year:04}-{month:02}-{day:02}T23:59:59.999999Z"
                    try:
                        expected = datetime(year, month, day, 23, 59, 59, 999999, tzinfo=UTC)
                    except ValueError:
                        expected = None
                    conn.execute("DELETE FROM datas")
                    if expected is None:
                        with pytest.raises(sqlite3.IntegrityError):
                            conn.execute("INSERT INTO datas VALUES (?,NULL)", (value,))
                    else:
                        conn.execute("INSERT INTO datas VALUES (?,NULL)", (value,))
                        assert conn.execute("SELECT autorizado_em FROM datas").fetchone()[0] == (
                            utc_text(expected)
                        )
