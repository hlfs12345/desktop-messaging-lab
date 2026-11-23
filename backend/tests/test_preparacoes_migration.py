"""Upgrade real 0006 com selecoes preservadas e downgrade sem perda."""

import test_campanhas as campaigns
from alembic import command
from sqlalchemy import text

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision
from app.persistencia.models import Base

cadastro = campaigns.cadastro
consentimento = campaigns.consentimento
rascunho = campaigns.rascunho


def test_upgrade_0006_com_dados(rascunho, tmp_path):
    c, app, _, _, identity, *_ = rascunho
    item = campaigns.criar(rascunho)
    assert campaigns.selecionar(c, item, identity).status_code == 200
    settings = Settings("test", tmp_path / "origem6")
    settings.data_dir.mkdir(parents=True)
    settings.database_path.touch()
    db = Database(settings.database_path)
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.upgrade(config, "0006_campanhas_rascunho")
    with app.state.contatos.repo.transacao() as s, db.engine.begin() as conn:
        names = set(
            conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").scalars()
        )
        before = {}
        columns = {}
        conn.exec_driver_sql("DELETE FROM estado_autenticacao")
        for table in Base.metadata.sorted_tables:
            if table.name not in names:
                continue
            columns[table.name] = ",".join(
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")
            )
            rows = s.execute(text(f"SELECT {columns[table.name]} FROM {table.name}")).all()
            before[table.name] = [tuple(row) for row in rows]
            if rows:
                conn.exec_driver_sql(
                    "INSERT INTO "
                    + table.name
                    + " VALUES ("
                    + ",".join("?" for _ in rows[0])
                    + ")",
                    before[table.name],
                )
    db.close()
    p = Persistence(settings)
    p.start()
    try:
        with p.database.transaction() as s:
            for name, rows in before.items():
                assert [
                    tuple(x) for x in s.execute(text(f"SELECT {columns[name]} FROM {name}"))
                ] == rows
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        snapshots = list(settings.snapshots_dir.glob("*.sqlite3"))
        assert len(snapshots) == 1 and read_revision(snapshots[0]) == "0006_campanhas_rascunho"
    finally:
        p.close()


def test_downgrade_vazio(settings):
    p = Persistence(settings)
    p.start()
    try:
        with bundled_migrations() as location, p.database.engine.begin() as conn:
            config = migration_config(location)
            config.attributes["connection"] = conn
            command.downgrade(config, "0006_campanhas_rascunho")
        assert read_revision(settings.database_path) == "0006_campanhas_rascunho"
    finally:
        p.close()
