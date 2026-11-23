"""Atualização de uma instalação 0001 real, com credenciais e snapshot."""

import sqlite3
from contextlib import closing

from alembic import command
from sqlalchemy import text

from app.auth.repository import AuthRepository
from app.auth.schemas import BootstrapInput, LoginInput
from app.auth.service import AuthService
from app.persistencia.database import Database
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision


def test_upgrade_0001_preserva_autenticacao_snapshot_reabertura(settings):
    settings.data_dir.mkdir(parents=True)
    settings.database_path.touch()
    db = Database(settings.database_path)
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_autenticacao_local")
    auth = AuthService(AuthRepository(db))
    auth.initialize()
    password = "frase fictícia para migração 489!"
    user = auth.bootstrap(BootstrapInput(nome="Teste", login="teste", senha=password))
    issued = auth.login(LoginInput(login="teste", senha=password))
    assert issued.usuario.id == user.id
    with db.transaction() as s:
        before = s.execute(text("SELECT id,senha_hash,login_normalizado FROM usuarios")).all()
    auth.close()
    db.close()
    persistence = Persistence(settings)
    persistence.start()
    try:
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        with persistence.database.transaction() as s:
            assert (
                s.execute(text("SELECT id,senha_hash,login_normalizado FROM usuarios")).all()
                == before
            )
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
        snapshots = list(settings.snapshots_dir.glob("*.sqlite3"))
        assert len(snapshots) == 1
        assert read_revision(snapshots[0]) == "0001_autenticacao_local"
        with closing(sqlite3.connect(snapshots[0])) as source:
            assert source.execute("SELECT id FROM usuarios").fetchone()[0] == user.id
        fresh = AuthService(AuthRepository(persistence.database))
        fresh.initialize()
        assert not fresh.setup_needed()
        assert fresh.login(LoginInput(login="teste", senha=password)).usuario.id == user.id
        fresh.close()
    finally:
        persistence.close()
    persistence.start()
    persistence.close()
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1
