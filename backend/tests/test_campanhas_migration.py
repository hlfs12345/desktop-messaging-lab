"""Upgrade 0005 com dados de todos os módulos e downgrade conservador."""

import sqlite3
from contextlib import closing

import pytest
import test_campanhas
from alembic import command
from sqlalchemy import text
from test_campanhas import criar, selecionar

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision

cadastro = test_campanhas.cadastro
consentimento = test_campanhas.consentimento
rascunho = test_campanhas.rascunho


def test_upgrade_0005_preserva_dados_e_snapshot(rascunho, tmp_path):
    _, app, _, _, _, _, _, f = rascunho
    import test_consentimentos

    test_consentimentos.registrar(f)
    settings = Settings("test", tmp_path / "upgrade-campanhas")
    settings.data_dir.mkdir(parents=True)
    with closing(sqlite3.connect(app.state.persistence.settings.database_path)) as source:
        with closing(sqlite3.connect(settings.database_path)) as target:
            source.backup(target)
    db = Database(settings.database_path)
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0005_contas_envio")
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
            for name, rows in before.items():
                columns = ",".join(rows[0]._mapping) if rows else "*"
                assert s.execute(text(f'SELECT {columns} FROM "{name}"')).all() == rows
            for name in ("campanhas", "campanha_listas", "campanha_selecoes"):
                assert s.execute(text("SELECT count(*) FROM " + name)).scalar() == 0
            assert s.execute(text("PRAGMA foreign_key_check")).all() == []
        assert read_revision(settings.database_path) == "0012_telegram_recepcao"
        snapshots = list(settings.snapshots_dir.glob("*.sqlite3"))
        assert len(snapshots) == 1
        assert read_revision(snapshots[0]) == "0005_contas_envio"
    finally:
        persistence.close()
    persistence.start()
    persistence.close()
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1


def test_downgrade_recusa_rascunhos_selecoes_e_preserva_historico(rascunho):
    c, app, _, _, identity, _, _, _ = rascunho
    item = criar(rascunho)
    assert selecionar(c, item, identity).status_code == 200
    before = c.get("/campanhas/" + item["id"]).json()
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        db = app.state.persistence.database
        with bundled_migrations() as location, db.engine.begin() as connection:
            config = migration_config(location)
            config.attributes["connection"] = connection
            command.downgrade(config, "0005_contas_envio")
    assert c.get("/campanhas/" + item["id"]).json() == before
    assert c.get("/campanhas/" + item["id"] + "/revisao").json()["itens"][0]["selecionada"]
    assert read_revision(app.state.persistence.settings.database_path) == "0012_telegram_recepcao"
