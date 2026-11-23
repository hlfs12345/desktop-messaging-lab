"""Upgrade real de 0003 com consentimentos, snapshot e reabertura."""

import sqlite3
from contextlib import closing

import test_consentimentos
import test_contatos
from alembic import command
from sqlalchemy import text

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision

cadastro = test_contatos.cadastro
consentimento = test_consentimentos.consentimento


def test_upgrade_preserva_todas_as_linhas_0003(consentimento, tmp_path):
    _, app, _, _, _, _, _ = consentimento
    test_consentimentos.registrar(consentimento)
    settings = Settings("test", tmp_path / "upgrade-isolado")
    settings.data_dir.mkdir(parents=True)
    with closing(sqlite3.connect(app.state.persistence.settings.database_path)) as source:
        with closing(sqlite3.connect(settings.database_path)) as target:
            source.backup(target)
    db = Database(settings.database_path)
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0003_consentimentos")
    with db.transaction() as s:
        names = (
            s.execute(
                text(
                    "SELECT name FROM sqlite_schema WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        before = {name: s.execute(text('SELECT * FROM "' + name + '"')).all() for name in names}
    db.close()
    persistence = Persistence(settings)
    persistence.start()
    try:
        with persistence.database.transaction() as s:
            for name in names:
                assert s.execute(text('SELECT * FROM "' + name + '"')).all() == before[name]
            assert s.execute(text("SELECT count(*) FROM templates")).scalar() == 0
            assert s.execute(text("SELECT count(*) FROM template_versoes")).scalar() == 0
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        snapshots = list(settings.snapshots_dir.glob("*.sqlite3"))
        assert len(snapshots) == 1
        assert read_revision(snapshots[0]) == "0003_consentimentos"
    finally:
        persistence.close()
    persistence.start()
    persistence.close()
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1
