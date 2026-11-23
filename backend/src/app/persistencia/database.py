"""Conexões síncronas curtas e transações explícitas no Python 3.14."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.persistencia.errors import DatabaseAccessError, PersistenceError


def make_engine(path: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=path.as_uri(), query={"mode": "rw", "uri": "true"}),
        connect_args={"autocommit": False, "check_same_thread": False, "timeout": 5.0},
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure(connection: sqlite3.Connection, _record: object) -> None:
        # Nenhum trabalho de aplicação foi iniciado: os PRAGMAs abaixo precisam
        # de autocommit SQLite. Restaurar False inicia a transação moderna.
        connection.autocommit = True
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
                mode = cursor.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                cursor.execute("PRAGMA synchronous=FULL")
                foreign_keys = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
                timeout = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
                synchronous = cursor.execute("PRAGMA synchronous").fetchone()[0]
                if (mode.lower(), foreign_keys, timeout, synchronous) != ("delete", 1, 5000, 2):
                    raise DatabaseAccessError(
                        "A configuração obrigatória do SQLite não foi aplicada."
                    )
            finally:
                cursor.close()
        finally:
            connection.autocommit = False

    return engine


class Database:
    def __init__(self, path: Path) -> None:
        self.engine = make_engine(path)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.closed = False
        self.schema_validator = None

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        if self.closed:
            raise DatabaseAccessError("A persistência está encerrada.")
        with self._sessions.begin() as session:
            yield session

    def probe(self, expected_revision: str | None = None) -> bool:
        if self.closed:
            return False
        try:
            with self.engine.connect() as connection:
                if self.schema_validator is not None:
                    self.schema_validator(connection, dados=False)
                revisions = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all()
                return revisions == ([(expected_revision,)] if expected_revision else [])
        except SQLAlchemyError, PersistenceError:
            return False

    def close(self) -> None:
        self.closed = True
        self.engine.dispose()
