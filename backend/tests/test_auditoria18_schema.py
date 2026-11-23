"""Reproducoes dos achados High; somente SQLite descartavel."""

import sqlite3
from contextlib import closing

import pytest
import test_autorizacoes_utc_migration as a1
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.persistencia.database import Database
from app.persistencia.errors import SchemaError
from app.persistencia.lifecycle import Persistence

vazia = a1.vazia
cadastro = a1.cadastro
consentimento = a1.consentimento
rascunho = a1.rascunho
pronta = a1.pronta
simulavel = a1.simulavel
populada_0008 = a1.populada_0008
povoada = a1.povoada


def test_0010_a1_nao_pode_anunciar_prontidao(vazia):
    db, settings, _ = vazia
    m = a1.modulo()
    with db.engine.begin() as conn:
        for sql in m.PROTECOES.values():
            conn.exec_driver_sql(sql)
        conn.exec_driver_sql("UPDATE alembic_version SET version_num=?", (a1.HEAD,))
    antes = a1.estado(db)
    p = Persistence(settings)
    try:
        with pytest.raises(SchemaError, match="intermedi"):
            p.start()
        assert not p.ready and not p.probe()
    finally:
        p.close()
    assert a1.estado(db) == antes


def test_predicado_real_do_diario_usa_busca_composta(vazia):
    db, settings, _ = vazia
    a1.migrar(db, settings)
    m = a1.modulo()
    sql = m.TRIGGERS_OPERACIONAIS["campanha_execucao_transicoes_origem"][1]
    sql = sql[sql.index("SELECT 1 FROM campanha_execucao_transicoes h") :]
    sql = sql[: sql.index(")) BEGIN")]
    import re

    sql = re.sub(r"NEW\.([a-z_]+)", r":\1", sql)
    args = dict(
        execucao_id="e",
        item_id="i",
        tentativa_id=None,
        entidade="item",
        versao=2,
        origem="pendente",
    )
    with db.engine.begin() as conn:
        plan = conn.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, args).all()
    assert any("ix_transicoes_entidade_versao" in row[-1] for row in plan), plan


@pytest.mark.parametrize(
    "sql",
    [
        None,
        "DROP INDEX ix_transicoes_entidade_versao",
        "DROP TRIGGER campanha_execucao_transicoes_origem",
        "DROP TRIGGER consentimento_suspensoes_confirmacao",
        "ALTER TABLE campanha_execucoes ADD COLUMN desconhecida TEXT",
        "DROP TABLE consentimento_suspensoes",
        "ALTER TABLE usuarios ADD COLUMN desconhecida TEXT",
        "CREATE TABLE desconhecida(id TEXT)",
        "DROP TABLE campanha_execucao_transicoes",
    ],
)
def test_assinatura_final_startup_e_probe_sem_reparo(vazia, sql):
    db, settings, _ = vazia
    a1.migrar(db, settings)
    if sql:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(sql)
    antes = a1.estado(db)
    p = Persistence(settings)
    try:
        if sql:
            with pytest.raises(SchemaError, match="desconhecida"):
                p.start()
            assert not p.ready and not p.probe()
        else:
            p.start()
            assert p.ready and p.probe()
            with db.engine.begin() as conn:
                conn.exec_driver_sql("DROP INDEX ix_transicoes_entidade_versao")
            assert not p.probe()
            # Somente a mutacao da fixture, sem reparo do probe.
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    a1.modulo().INDICES_OPERACIONAIS["ix_transicoes_entidade_versao"][1]
                )
            assert p.probe()
    finally:
        p.close()
    assert a1.estado(db) == antes


def test_startup_real_a1_preserva_historico_fotografias_e_blobs(povoada, authorization, tmp_path):
    db, settings, _, _ = povoada
    with db.engine.begin() as conn:
        for sql in a1.modulo().PROTECOES.values():
            conn.exec_driver_sql(sql)
        conn.exec_driver_sql("UPDATE alembic_version SET version_num=?", (a1.HEAD,))
    antes = a1.estado(db)
    copied = Settings("test", tmp_path / "startup-a1")
    copied.data_dir.mkdir(parents=True)
    with (
        closing(sqlite3.connect(settings.database_path)) as src,
        closing(sqlite3.connect(copied.database_path)) as dst,
    ):
        src.backup(dst)
    app = create_app(copied, authorization=authorization)
    with pytest.raises(RuntimeError, match="intermediaria A1"), TestClient(app):
        pytest.fail("Startup intermediario foi liberado")
    assert app.state.startup_error.__name__ == "IntermediateSchemaError"
    assert not hasattr(app.state, "execution_runtime")
    client = TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + authorization.token}
    )
    try:
        assert client.get("/health").status_code == 503
    finally:
        client.close()
    assert a1.estado(db) == antes
    other = Database(copied.database_path)
    try:
        assert a1.estado(other) == antes
    finally:
        other.close()


def test_checkpoint_operacional_anterior_high_tambem_e_intermediario(vazia):
    db, settings, _ = vazia
    a1.migrar(db, settings)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_transicoes_entidade_versao")
        conn.exec_driver_sql("DROP TABLE consentimento_suspensoes")
    antes = a1.estado(db)
    p = Persistence(settings)
    with pytest.raises(SchemaError, match="Estrutura Telegram desconhecida ou incompleta"):
        p.start()
    assert not p.ready and not p.probe()
    assert a1.estado(db) == antes


def test_downgrade_recusa_diario_sem_espelho_sqlite(vazia):
    db, settings, _ = vazia
    a1.migrar(db, settings)
    directory = settings.database_path.with_suffix(".optouts")
    directory.mkdir()
    evidence = directory / "intencao-nao-espelhada.json"
    evidence.write_bytes(b"intencao de teste")
    before = a1.estado(db)
    with pytest.raises(RuntimeError, match="diario de descadastramento"):
        a1.anterior.revision(db, a1.ORIGEM, downgrade=True)
    assert a1.estado(db) == before
    assert evidence.read_bytes() == b"intencao de teste"
