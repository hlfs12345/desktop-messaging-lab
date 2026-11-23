"""Transações curtas compartilhadas pelos serviços de cadastro/importação."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.contatos.errors import NegocioError, ausente, banco_ocupado
from app.contatos.models import Auditoria
from app.persistencia.errors import PersistenceError


def agora():
    return datetime.now(UTC)


def uuid():
    return str(uuid4())


class CadastroRepository:
    def __init__(self, database):
        self.database = database
        self.lock = RLock()

    @contextmanager
    def transacao(self, *, espera=None):
        acquired = False
        try:
            acquired = self.lock.acquire() if espera is None else self.lock.acquire(timeout=espera)
            if not acquired:
                raise banco_ocupado()
            with self.database.transaction() as session:
                if espera is not None:
                    # NullPool: esta espera curta pertence apenas à conexão do receptor.
                    session.connection().exec_driver_sql(
                        f"PRAGMA busy_timeout={max(1, int(espera * 1000))}"
                    )
                yield session
        except IntegrityError:
            raise NegocioError(
                "duplicidade", "Registro duplicado ou associação inválida.", 409
            ) from None
        except (SQLAlchemyError, PersistenceError) as error:
            code = getattr(getattr(error, "orig", None), "sqlite_errorcode", 0) or 0
            if code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise banco_ocupado() from None
            raise NegocioError(
                "persistencia_indisponivel",
                "Não foi possível acessar os dados. Tente novamente.",
                503,
            ) from None
        finally:
            if acquired:
                self.lock.release()

    def obter(self, session, model, identifier):
        item = session.get(model, identifier)
        if item is None:
            raise ausente()
        return item

    def auditar(self, session, usuario, acao, entidade, identifier, **resumo):
        session.add(
            Auditoria(
                id=uuid(),
                usuario_id=usuario,
                acao=acao,
                entidade=entidade,
                entidade_id=identifier,
                resumo_json=json.dumps(resumo),
                ocorrido_em=agora(),
            )
        )

    def revisao(self, session):
        # Todas as mutações do cadastro acrescentam auditoria na mesma transação.
        return session.scalar(select(func.count()).select_from(Auditoria))
