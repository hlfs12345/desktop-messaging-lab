"""Primeira revisão real e atualização de bancos da Entrega 5."""

import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select

from app.auth.models import BOOTSTRAP_ID, Base, EstadoAutenticacao
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision


@pytest.mark.parametrize("previous", ["missing", "empty", "delivery5"])
def test_real_migration_and_snapshot(settings, previous):
    if previous != "missing":
        settings.data_dir.mkdir(parents=True)
        with closing(sqlite3.connect(settings.database_path)) as db, db:
            if previous == "delivery5":
                db.execute(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
    persistence = Persistence(settings)
    persistence.start()
    try:
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        with persistence.database.transaction() as session:
            state = session.scalar(select(EstadoAutenticacao))
            assert state.id == BOOTSTRAP_ID and state.configurado_em is None
        inspector = inspect(persistence.database.engine)
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "usuarios",
            "sessoes",
            "estado_autenticacao",
            "contatos",
            "telefones_contato",
            "listas",
            "lista_contatos",
            "importacoes",
            "importacao_linhas",
            "auditoria",
            "escopos_comunicacao",
            "identidades_contato",
            "eventos_consentimento",
            "preferencias_contato",
            "templates",
            "template_versoes",
            "contas_envio",
            "campanhas",
            "campanha_listas",
            "campanha_selecoes",
            "campanha_contexto_criptografico",
            "campanha_preparacoes",
            "campanha_preparacao_itens",
            "campanha_autorizacoes_simulacao",
            "campanha_execucoes",
            "campanha_destinatarios",
            "campanha_execucao_itens",
            "tentativas_envio",
            "campanha_execucao_transicoes",
            "consentimento_suspensoes",
            "identidades_telegram",
            "inscricoes_conta",
            "eventos_telegram",
            "telegram_recibos",
        }
        assert {item["name"] for item in inspector.get_indexes("sessoes")} == {
            "uq_sessoes_token_hash",
            "ix_sessoes_usuario_expiracao",
        }
        assert inspector.get_foreign_keys("sessoes")[0]["options"]["ondelete"] == "RESTRICT"
        with persistence.database.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        snapshots = list(settings.snapshots_dir.glob("*.sqlite3"))
        assert len(snapshots) == (previous != "missing")
        if snapshots:
            assert read_revision(snapshots[0]) is None
    finally:
        persistence.close()
    persistence.start()
    persistence.close()
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == (previous != "missing")


def test_disposable_downgrade_and_upgrade(settings):
    persistence = Persistence(settings)
    persistence.start()
    try:
        with bundled_migrations() as location:
            config = migration_config(location)
            with persistence.database.engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "base")
            assert read_revision(settings.database_path) is None
            with persistence.database.engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            assert read_revision(settings.database_path) == "0012_telegram_recepcao"
    finally:
        persistence.close()
