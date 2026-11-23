"""Gravações curtas; nenhum hash de senha é calculado dentro da transação."""

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, literal, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.errors import configured, invalid_credentials, invalid_session, unavailable
from app.auth.models import BOOTSTRAP_ID, EstadoAutenticacao, Sessao, Usuario, UTCText
from app.persistencia.database import Database
from app.persistencia.errors import PersistenceError


class AuthRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        try:
            with self.database.transaction() as session:
                yield session
        except SQLAlchemyError, PersistenceError, ValueError, TypeError:
            raise unavailable() from None

    def _state(self, session: Session) -> EstadoAutenticacao:
        states = session.scalars(select(EstadoAutenticacao)).all()
        if len(states) != 1 or states[0].id != BOOTSTRAP_ID:
            raise unavailable()
        state = states[0]
        if state.configurado_em is None and session.scalar(
            select(func.count()).select_from(Usuario)
        ):
            raise unavailable()
        return state

    def setup_needed(self) -> bool:
        with self.transaction() as session:
            return self._state(session).configurado_em is None

    def initialize(self, now: datetime, *, busy_timeout_ms=None) -> None:
        with self.transaction() as session:
            if busy_timeout_ms is not None:
                session.connection().exec_driver_sql(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            self._state(session)
            # max evita violar a cronologia em uma correção do relógio Windows.
            session.execute(
                update(Sessao)
                .where(Sessao.revogado_em.is_(None))
                .values(revogado_em=func.max(Sessao.criado_em, literal(now, type_=UTCText())))
            )

    def bootstrap(self, name: str, login: str, password_hash: str, now: datetime) -> Usuario:
        with self.transaction() as session:
            changed = session.execute(
                update(EstadoAutenticacao)
                .where(
                    EstadoAutenticacao.id == BOOTSTRAP_ID,
                    EstadoAutenticacao.configurado_em.is_(None),
                )
                .values(configurado_em=now)
            ).rowcount
            if changed != 1:
                self._state(session)
                raise configured()
            if session.scalar(select(func.count()).select_from(Usuario)):
                raise unavailable()
            user = Usuario(
                id=str(uuid4()),
                nome=name,
                login=login,
                login_normalizado=login.lower(),
                senha_hash=password_hash,
                perfil="administrador",
                ativo=1,
                criado_em=now,
                atualizado_em=now,
            )
            session.add(user)
            session.flush()
        return user

    def find_user(self, login: str) -> Usuario | None:
        with self.transaction() as session:
            self._state(session)
            return session.scalar(select(Usuario).where(Usuario.login_normalizado == login))

    def issue(
        self, user: Usuario, new_hash: str, token_hash: str, now: datetime, expires: datetime
    ) -> None:
        with self.transaction() as session:
            # Primeira instrução é uma escrita condicional: serializa logins e
            # rejeita alteração de senha/desativação ocorrida durante Argon2.
            changed = session.execute(
                update(Usuario)
                .where(
                    Usuario.id == user.id,
                    Usuario.ativo == 1,
                    Usuario.senha_hash == user.senha_hash,
                )
                .values(
                    senha_hash=new_hash,
                    atualizado_em=max(now, user.atualizado_em)
                    if new_hash != user.senha_hash
                    else user.atualizado_em,
                )
            ).rowcount
            if changed != 1:
                raise invalid_credentials()
            if self._state(session).configurado_em is None:
                raise unavailable()
            session.execute(
                update(Sessao)
                .where(
                    Sessao.usuario_id == user.id,
                    Sessao.revogado_em.is_(None),
                )
                .values(revogado_em=func.max(Sessao.criado_em, literal(now, type_=UTCText())))
            )
            session.add(
                Sessao(
                    id=str(uuid4()),
                    usuario_id=user.id,
                    token_hash=token_hash,
                    criado_em=now,
                    expira_em=expires,
                )
            )

    def authenticated(
        self,
        token_hash: str,
        now: datetime,
        *,
        session: Session | None = None,
    ) -> tuple[Usuario, datetime]:
        with self.transaction() if session is None else nullcontext(session) as session:
            self._state(session)
            row = session.execute(
                select(Usuario, Sessao.expira_em)
                .join(
                    Sessao,
                    Sessao.usuario_id == Usuario.id,
                )
                .where(
                    Sessao.token_hash == token_hash,
                    Sessao.revogado_em.is_(None),
                    Sessao.expira_em > now,
                    Usuario.ativo == 1,
                )
            ).first()
            if row is None:
                raise invalid_session()
            return row[0], row[1]

    def logout(self, token_hash: str, now: datetime) -> None:
        with self.transaction() as session:
            session.execute(
                update(Sessao)
                .where(
                    Sessao.token_hash == token_hash,
                    Sessao.revogado_em.is_(None),
                )
                .values(revogado_em=func.max(Sessao.criado_em, literal(now, type_=UTCText())))
            )
            self._state(session)
