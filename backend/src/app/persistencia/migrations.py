"""Validação de histórico e upgrades preservando um snapshot consistente."""

import sqlite3
import time
from contextlib import AbstractContextManager, closing
from datetime import UTC, datetime
from functools import cache
from importlib.resources import as_file, files
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.persistencia.errors import (
    DatabaseAccessError,
    IntermediateSchemaError,
    MigrationError,
    SchemaError,
    SnapshotError,
)


def _signature(connection, tokenize):
    # SQL da tabela 0009 e reconhecido separadamente nas duas variantes conhecidas.
    return {
        (kind, name, table): tokenize(sql)
        for kind, name, table, sql in connection.exec_driver_sql(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema"
        )
        if (kind, name) != ("table", "campanha_autorizacoes_simulacao")
        and not name.startswith("sqlite_stat")
    }


@cache
def _final_signature(location: str, revision: str):
    """Derivar das migracoes oficiais, em RAM, sem segunda definicao de DDL."""
    config = migration_config(Path(location))
    module = ScriptDirectory.from_config(config).get_revision("0010_autorizacoes_utc").module
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"autocommit": False})
    try:
        with engine.connect() as connection:
            raw = connection.connection.driver_connection
            raw.autocommit = True
            raw.execute("PRAGMA foreign_keys=ON").close()
            raw.autocommit = False
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
            return _signature(connection, module._sql)
    finally:
        engine.dispose()


def schema_validator(config: Config, current: str | None):
    if current not in {
        "0010_autorizacoes_utc",
        "0011_telegram_optin",
        "0012_telegram_recepcao",
        "0013_telegram_consentimento",
        "0014_telegram_campanhas",
        "0015_telegram_envio_unitario",
        "0016_telegram_reativacao",
        "0017_telegram_lote_preparacao",
        "0018_telegram_executor_lote",
        "0019_whatsapp_envio_unitario",
    }:
        return None
    module = ScriptDirectory.from_config(config).get_revision("0010_autorizacoes_utc").module
    expected = _final_signature(config.get_main_option("script_location"), current)

    def validate(connection, *, dados=True):
        connection.exec_driver_sql("SELECT 1")
        actual = _signature(connection, module._sql)
        history = ("table", "alembic_version", "alembic_version")
        # A Entrega 5 usava esta declaracao exata, ja reconhecida por read_revision.
        # Aceitar a variante conhecida nao permite checks/colunas desconhecidos.
        legacy_history = module._sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        if actual.get(history) == legacy_history:
            actual[history] = expected[history]
        if current in {"0018_telegram_executor_lote", "0019_whatsapp_envio_unitario"}:
            if (
                actual != expected
                or connection.exec_driver_sql("PRAGMA quick_check").all() != [("ok",)]
                or connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            ):
                raise SchemaError(
                    "Estrutura Telegram desconhecida ou incompleta. Preserve os dados."
                )
            return
        try:
            module._validar(connection, protegida=True, dados=dados)
            if actual != expected:
                raise RuntimeError("Dependencias desconhecidas")
            return
        except RuntimeError:
            pass
        if current in {
            "0011_telegram_optin",
            "0012_telegram_recepcao",
            "0013_telegram_consentimento",
            "0014_telegram_campanhas",
            "0015_telegram_envio_unitario",
            "0016_telegram_reativacao",
            "0017_telegram_lote_preparacao",
            "0018_telegram_executor_lote",
            "0019_whatsapp_envio_unitario",
        }:
            raise SchemaError("Estrutura Telegram desconhecida ou incompleta. Preserve os dados.")
        # Checkpoint operacional anterior as correcoes, igualmente nao reaplicavel.
        before_high = {
            k: v
            for k, v in expected.items()
            if k[2] != "consentimento_suspensoes" and k[1] != "ix_transicoes_entidade_versao"
        }
        if actual == before_high:
            raise IntermediateSchemaError(
                "Revisao 0010 intermediaria anterior as correcoes High. Preserve os dados "
                "e solicite recuperacao supervisionada."
            )
        try:
            module._validar(connection, protegida=True, operacional=False, dados=dados)
            known_a1 = {
                k: v for k, v in expected.items() if k not in module._objetos_operacionais()
            }
            if actual != known_a1:
                raise RuntimeError("Dependencias desconhecidas")
        except RuntimeError:
            raise SchemaError(
                "Estrutura 0010 desconhecida ou incompleta. Preserve os dados para revisao."
            ) from None
        raise IntermediateSchemaError(
            "Revisao 0010 intermediaria A1. Persistencia indisponivel; preserve os dados "
            "e solicite recuperacao supervisionada."
        )

    return validate


def read_revision(path: Path) -> str | None:
    """Somente leitura; nunca cria ou carimba um banco desconhecido."""
    if not path.exists():
        return None
    try:
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=True)
        ) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise SchemaError("O banco não passou na verificação de integridade.")
            objects = connection.execute(
                "SELECT name, type FROM sqlite_schema WHERE substr(name, 1, 7) != 'sqlite_'"
            ).fetchall()
            tables = {name for name, kind in objects if kind == "table"}
            current = None
            if ("alembic_version", "view") in objects:
                raise SchemaError("O histórico de esquema não é uma tabela válida.")
            if "alembic_version" in tables:
                columns = connection.execute("PRAGMA table_info(alembic_version)").fetchall()
                if len(columns) != 1 or (
                    columns[0][1] != "version_num"
                    or columns[0][2].upper() != "VARCHAR(32)"
                    or columns[0][3] != 1
                    or columns[0][5] != 1
                ):
                    raise SchemaError("O histórico de esquema possui estrutura inválida.")
                revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
                if len(revisions) > 1 or (
                    revisions
                    and (
                        not isinstance(revisions[0][0], str)
                        or not revisions[0][0]
                        or len(revisions[0][0]) > 32
                    )
                ):
                    raise SchemaError("O banco possui revisões incompatíveis.")
                current = revisions[0][0] if revisions else None
            if current is None and any(name != "alembic_version" for name, _kind in objects):
                raise SchemaError("O banco contém estruturas sem histórico válido.")
            return current
    except sqlite3.Error:
        raise DatabaseAccessError("Não foi possível inspecionar o banco local.") from None


def validate_history(config: Config, current: str | None) -> bool:
    """Retorna se há upgrade; revisões são comparadas pelo grafo, nunca pelo nome."""
    try:
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
        if len(heads) > 1:
            raise SchemaError("O executável possui histórico de migrações divergente.")
        head = heads[0] if heads else None
        if current is not None and scripts.get_revision(current) is None:
            raise SchemaError(
                "A versão do banco é desconhecida ou mais recente que este executável."
            )
        if current != head:
            if head is None:
                raise SchemaError("O esquema do banco é incompatível com este executável.")
            list(scripts.iterate_revisions(head, current))
        return current != head
    except CommandError:
        raise SchemaError(
            "O esquema do banco não pertence ao histórico deste executável."
        ) from None


def create_snapshot(path: Path, directory: Path) -> Path:
    """Backup SQLite, incluindo dados confirmados no journal/WAL; não copia o .db."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"{stamp}-{uuid4().hex}.sqlite3"
    partial = destination.with_suffix(".sqlite3.partial")
    deadline = time.monotonic() + 10

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError

    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Criação exclusiva: nunca sobrescrever uma cópia de recuperação.
        partial.touch(exist_ok=False)
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=True)
        try:
            target = sqlite3.connect(partial, autocommit=True)
            try:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise SnapshotError("A cópia de preservação não passou na verificação.")
            finally:
                target.close()
        finally:
            source.close()
        partial.rename(destination)
        return destination
    except OSError, sqlite3.Error:
        # Arquivos parciais são preservados para diagnóstico; não são backups válidos.
        raise SnapshotError("Não foi possível concluir a cópia de preservação.") from None


def migrate(
    engine: Engine,
    path: Path,
    snapshots_dir: Path,
    current: str | None,
    config: Config,
    *,
    existing: bool,
) -> Path | None:
    upgrade = validate_history(config, current)
    validator = schema_validator(config, current)
    if validator is not None:
        with engine.connect() as connection:
            validator(connection)
    snapshot = create_snapshot(path, snapshots_dir) if existing and upgrade else None
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.ensure_version(config)
            if upgrade:
                command.upgrade(config, "head")
        if validate_history(config, read_revision(path)):
            raise MigrationError("A migração não alcançou a versão esperada.")
        validator = schema_validator(config, read_revision(path))
        if validator is not None:
            with engine.connect() as connection:
                validator(connection)
    except Exception:
        raise MigrationError("A migração falhou; os arquivos foram preservados.") from None
    finally:
        config.attributes.pop("connection", None)
    return snapshot


def migration_config(location: Path) -> Config:
    config = Config()
    # ConfigParser interpola %, que também pode fazer parte de caminhos Windows.
    config.set_main_option("script_location", str(location).replace("%", "%%"))
    return config


def bundled_migrations() -> AbstractContextManager[Path]:
    """Recursos do pacote; funciona sem localizar a raiz do repositório."""
    return as_file(files("app.persistencia").joinpath("alembic"))
