"""Upgrade de banco 0002 com dados e integridade real."""

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.persistencia.database import Database
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision


def test_upgrade_0002_preserva_cadastro_sem_autorizacoes_e_reabre(settings):
    settings.data_dir.mkdir(parents=True)
    settings.database_path.touch()
    db = Database(settings.database_path)
    contact, phone = str(uuid4()), str(uuid4())
    stamp = "2026-01-01T00:00:00.000000Z"
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.upgrade(config, "0002_contatos_listas_importacoes")
        connection.execute(
            text(
                "INSERT INTO contatos (id,nome,nome_busca,ativo,versao,criado_em,atualizado_em) "
                "VALUES (:id,'Teste','teste',1,4,:t,:t)"
            ),
            {"id": contact, "t": stamp},
        )
        connection.execute(
            text(
                "INSERT INTO telefones_contato "
                "(id,contato_id,telefone_e164,ativo,versao,criado_em,atualizado_em) "
                "VALUES (:id,:c,'+5511912345678',1,2,:t,:t)"
            ),
            {"id": phone, "c": contact, "t": stamp},
        )
    with db.transaction() as s:
        before = s.execute(text("SELECT * FROM telefones_contato")).all()
    db.close()
    persistence = Persistence(settings)
    persistence.start()
    try:
        with persistence.database.transaction() as s:
            assert s.execute(text("SELECT * FROM telefones_contato")).all() == before
            assert s.execute(
                text("SELECT id,versao,consentimento_versao,bloqueio_global_em FROM contatos")
            ).one() == (contact, 4, 0, None)
            for table in (
                "escopos_comunicacao",
                "identidades_contato",
                "eventos_consentimento",
                "preferencias_contato",
            ):
                assert s.execute(text("SELECT count(*) FROM " + table)).scalar() == 0
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
        with pytest.raises(IntegrityError), persistence.database.transaction() as s:
            s.execute(text("UPDATE contatos SET consentimento_versao=-1"))
        with pytest.raises(IntegrityError), persistence.database.transaction() as s:
            s.execute(
                text(
                    "INSERT INTO preferencias_contato VALUES "
                    "('inexistente','inexistente','promocoes','autorizado','inexistente',:t)"
                ),
                {"t": stamp},
            )
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1
    finally:
        persistence.close()
    persistence.start()
    persistence.close()
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1
