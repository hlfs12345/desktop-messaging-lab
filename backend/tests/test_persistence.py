"""Integração real SQLite/Alembic em bancos temporários, sem tabelas do produto."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.errors import (
    DatabaseAccessError,
    InstanceInUseError,
    MigrationError,
    SchemaError,
    SnapshotError,
)
from app.persistencia.instance_lock import InstanceLock
from app.persistencia.lifecycle import Persistence
from app.persistencia.migrations import (
    bundled_migrations,
    create_snapshot,
    migrate,
    migration_config,
    read_revision,
    validate_history,
)


@pytest.fixture
def scripts(tmp_path: Path) -> Path:
    destination = tmp_path / "migration scripts % ficticios"
    with bundled_migrations() as source:
        shutil.copytree(
            source, destination, ignore=shutil.ignore_patterns("versions", "__pycache__")
        )
    (destination / "versions").mkdir()
    (destination / "versions" / "r1.py").write_text(
        """from alembic import op
import sqlalchemy as sa
revision = "r1"
down_revision = None

def upgrade():
    op.create_table("teste_pais", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("valor", sa.Text, nullable=False, unique=True))
    op.create_table("teste_filhos", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("pai_id", sa.Integer, sa.ForeignKey("teste_pais.id"), nullable=False))

def downgrade():
    op.drop_table("teste_filhos")
    op.drop_table("teste_pais")
""",
        encoding="utf-8",
    )
    return destination


def upgrade(settings: Settings, scripts: Path) -> Path | None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(settings.database_path)
    lock.acquire()
    database = None
    try:
        existing = settings.database_path.exists()
        current = read_revision(settings.database_path)
        config = migration_config(scripts)
        validate_history(config, current)
        if not existing:
            settings.database_path.touch(exist_ok=False)
        database = Database(settings.database_path)
        return migrate(
            database.engine,
            settings.database_path,
            settings.snapshots_dir,
            current,
            config,
            existing=existing,
        )
    finally:
        if database:
            database.close()
        lock.release()


def add_revision(scripts: Path, body: str, revision: str = "r2", parent: str = "r1") -> None:
    (scripts / "versions" / f"{revision}.py").write_text(
        f'from alembic import op\nimport sqlalchemy as sa\nrevision = "{revision}"\n'
        f'down_revision = "{parent}"\ndef upgrade():\n{body}\n',
        encoding="utf-8",
    )


def test_new_database_contains_only_current_delivery_tables(settings: Settings) -> None:
    persistence = Persistence(settings)
    persistence.start()
    try:
        assert persistence.probe()
        with persistence.database.engine.connect() as connection:
            actual = set(
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                ).scalars()
            )
            assert actual == {
                "alembic_version",
                "usuarios",
                "sessoes",
                "estado_autenticacao",
                "contatos",
                "listas",
                "auditoria",
                "importacoes",
                "lista_contatos",
                "telefones_contato",
                "importacao_linhas",
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
            assert connection.exec_driver_sql("SELECT * FROM alembic_version").all() == [
                ("0012_telegram_recepcao",)
            ]
        assert not settings.snapshots_dir.exists()
    finally:
        persistence.close()
    assert not persistence.probe()
    persistence.start()
    persistence.close()


def test_repeated_start_does_not_release_active_lock(settings: Settings) -> None:
    persistence = Persistence(settings)
    persistence.start()
    try:
        with pytest.raises(InstanceInUseError):
            persistence.start()
        with pytest.raises(InstanceInUseError):
            InstanceLock(settings.database_path).acquire()
        assert persistence.probe()
    finally:
        persistence.close()


def test_pragmas_on_independent_simultaneous_connections(settings: Settings) -> None:
    persistence = Persistence(settings)
    persistence.start()
    try:
        database = persistence.database
        assert isinstance(database.engine.pool, NullPool)
        with database.engine.connect() as first, database.engine.connect() as second:
            assert first.connection.dbapi_connection is not second.connection.dbapi_connection
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
                assert connection.connection.dbapi_connection.autocommit is False
    finally:
        persistence.close()


def test_commit_rollback_integrity_and_reopen(settings: Settings, scripts: Path) -> None:
    assert upgrade(settings, scripts) is None
    database = Database(settings.database_path)
    try:
        with database.transaction() as session:
            session.execute(text("INSERT INTO teste_pais VALUES (1, 'ficticio')"))
        with pytest.raises(IntegrityError), database.transaction() as session:
            session.execute(text("INSERT INTO teste_pais VALUES (2, 'ficticio')"))
        with pytest.raises(IntegrityError), database.transaction() as session:
            session.execute(text("INSERT INTO teste_filhos VALUES (1, 999)"))
        with pytest.raises(ValueError), database.transaction() as session:
            session.execute(text("INSERT INTO teste_pais VALUES (3, 'rollback')"))
            raise ValueError("falha de teste")
        with database.transaction() as session:
            session.execute(text("INSERT INTO teste_filhos VALUES (1, 1)"))
        with database.engine.connect() as connection:
            raw = connection.connection.dbapi_connection
        with pytest.raises(sqlite3.ProgrammingError):
            raw.execute("SELECT 1")
    finally:
        database.close()
    assert upgrade(settings, scripts) is None  # repetição não cria snapshot
    reopened = Database(settings.database_path)
    try:
        with reopened.transaction() as session:
            assert session.execute(text("SELECT * FROM teste_pais")).all() == [(1, "ficticio")]
    finally:
        reopened.close()
    with pytest.raises(DatabaseAccessError), reopened.transaction():
        pass


def test_effective_migration_creates_consistent_snapshot(settings: Settings, scripts: Path) -> None:
    upgrade(settings, scripts)
    database = Database(settings.database_path)
    with database.transaction() as session:
        session.execute(text("INSERT INTO teste_pais VALUES (1, 'preservado')"))
    database.close()
    add_revision(scripts, '    op.add_column("teste_pais", sa.Column("extra", sa.Text))')
    snapshot = upgrade(settings, scripts)
    assert snapshot and read_revision(snapshot) == "r1"
    assert read_revision(settings.database_path) == "r2"
    backup = sqlite3.connect(snapshot)
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT * FROM teste_pais").fetchall() == [(1, "preservado")]
        assert [column[1] for column in backup.execute("PRAGMA table_info(teste_pais)")] == [
            "id",
            "valor",
        ]
    finally:
        backup.close()
    assert upgrade(settings, scripts) is None
    assert len(list(settings.snapshots_dir.glob("*.sqlite3"))) == 1


def test_failed_migration_rolls_back_ddl_and_preserves_snapshot(
    settings: Settings, scripts: Path
) -> None:
    upgrade(settings, scripts)
    original = settings.database_path.read_bytes()
    add_revision(
        scripts,
        '    op.create_table("teste_incompleta", sa.Column("id", sa.Integer))\n'
        '    raise RuntimeError("dados que nao devem aparecer no erro publico")',
    )
    with pytest.raises(MigrationError, match="arquivos foram preservados"):
        upgrade(settings, scripts)
    assert settings.database_path.read_bytes() == original
    assert read_revision(settings.database_path) == "r1"
    (snapshot,) = settings.snapshots_dir.glob("*.sqlite3")
    assert read_revision(snapshot) == "r1"
    backup = sqlite3.connect(snapshot)
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert (
            backup.execute(
                "SELECT name FROM sqlite_schema WHERE name='teste_incompleta'"
            ).fetchall()
            == []
        )
    finally:
        backup.close()


def test_failed_snapshot_prevents_migration(settings: Settings, scripts: Path) -> None:
    upgrade(settings, scripts)
    original = settings.database_path.read_bytes()
    settings.snapshots_dir.parent.mkdir(parents=True)
    settings.snapshots_dir.write_text("obstaculo ficticio", encoding="utf-8")
    add_revision(scripts, '    op.add_column("teste_pais", sa.Column("extra", sa.Text))')
    with pytest.raises(SnapshotError):
        upgrade(settings, scripts)
    assert settings.database_path.read_bytes() == original


@pytest.mark.parametrize("revision", ["futura", "desconhecida", ""])
def test_unknown_revisions_are_rejected_without_changes(
    settings: Settings, scripts: Path, revision: str
) -> None:
    upgrade(settings, scripts)
    database = Database(settings.database_path)
    with database.transaction() as session:
        session.execute(text("UPDATE alembic_version SET version_num=:value"), {"value": revision})
    database.close()
    original = settings.database_path.read_bytes()
    persistence = Persistence(settings)
    with pytest.raises(SchemaError):
        persistence.start()
    assert not persistence.probe()
    assert settings.database_path.read_bytes() == original
    lock = InstanceLock(settings.database_path)
    lock.acquire()
    lock.release()


def test_branch_and_multiple_heads_are_rejected(settings: Settings, scripts: Path) -> None:
    upgrade(settings, scripts)
    add_revision(scripts, "    pass", "r2a")
    add_revision(scripts, "    pass", "r2b")
    with pytest.raises(SchemaError):
        upgrade(settings, scripts)
    assert read_revision(settings.database_path) == "r1"
    assert not settings.snapshots_dir.exists()


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE inesperada (id INTEGER)",
        "CREATE VIEW inesperada AS SELECT 1",
        "CREATE TABLE alembic_version (coluna_errada TEXT)",
        "CREATE TABLE alembic_version (version_num INTEGER PRIMARY KEY)",
        "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)",
        "CREATE VIEW alembic_version AS SELECT 1 AS version_num",
        "CREATE TABLE sqlitex_inesperada (id INTEGER)",
    ],
)
def test_unversioned_structures_are_rejected(settings: Settings, ddl: str) -> None:
    settings.data_dir.mkdir(parents=True)
    connection = sqlite3.connect(settings.database_path)
    connection.execute(ddl)
    connection.close()
    original = settings.database_path.read_bytes()
    with pytest.raises(SchemaError):
        Persistence(settings).start()
    assert settings.database_path.read_bytes() == original


def test_corrupt_database_is_not_recreated(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    settings.database_path.write_bytes(b"arquivo ficticio que nao e SQLite")
    with pytest.raises(DatabaseAccessError):
        Persistence(settings).start()
    assert settings.database_path.read_bytes() == b"arquivo ficticio que nao e SQLite"


def test_empty_existing_file_can_be_initialized(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    settings.database_path.touch()
    persistence = Persistence(settings)
    persistence.start()
    persistence.close()
    assert read_revision(settings.database_path) == "0012_telegram_recepcao"


def test_database_busy_has_bounded_wait_and_rollback(settings: Settings, scripts: Path) -> None:
    upgrade(settings, scripts)
    database = Database(settings.database_path)
    holder = sqlite3.connect(settings.database_path, autocommit=True)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError), database.transaction() as session:
            session.execute(text("INSERT INTO teste_pais VALUES (1, 'ocupado')"))
        holder.execute("ROLLBACK")
        with database.transaction() as session:
            assert session.execute(text("SELECT count(*) FROM teste_pais")).scalar_one() == 0
    finally:
        holder.close()
        database.close()


def test_snapshot_preserves_committed_data(settings: Settings, scripts: Path) -> None:
    upgrade(settings, scripts)
    snapshot = create_snapshot(settings.database_path, settings.snapshots_dir)
    assert read_revision(snapshot) == "r1"
