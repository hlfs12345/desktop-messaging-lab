"""Casos de uso síncronos, executados pelo threadpool do FastAPI."""

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from argon2.exceptions import Argon2Error
from sqlalchemy.orm import Session

from app.auth.errors import invalid_credentials, invalid_session, unavailable
from app.auth.passwords import Passwords
from app.auth.rate_limit import Attempts, CryptoGate
from app.auth.repository import AuthRepository
from app.auth.schemas import BootstrapInput, LoginInput, LoginResult, PublicUser, SessionView

TOKEN_PATTERN = re.compile(r"edisk_u_[A-Za-z0-9_-]{43}")


def token_digest(token: str) -> str:
    if TOKEN_PATTERN.fullmatch(token) is None:
        raise invalid_session()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        attempts: Attempts | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.attempts = attempts or Attempts()
        self.gate = CryptoGate()
        self.passwords = Passwords()
        self._closed = threading.Event()
        self.session_changed = None
        self.session_started = None

    def initialize(self, *, busy_timeout_ms=None) -> None:
        self.repository.initialize(self.clock(), busy_timeout_ms=busy_timeout_ms)

    def close(self) -> None:
        self._closed.set()

    def _available(self) -> None:
        if self._closed.is_set():
            raise unavailable()

    def setup_needed(self) -> bool:
        self._available()
        return self.repository.setup_needed()

    def bootstrap(self, data: BootstrapInput) -> PublicUser:
        self._available()
        self.attempts.reserve("bootstrap:" + data.login.lower())
        with self.gate.enter():
            self._available()
            try:
                hashed = self.passwords.hash(data.senha)
            except Argon2Error:
                raise unavailable() from None
            self._available()
            user = self.repository.bootstrap(data.nome, data.login, hashed, self.clock())
        return PublicUser.model_validate(user)

    def login(self, data: LoginInput) -> LoginResult:
        self._available()
        key = self.attempts.reserve(data.login.lower())
        with self.gate.enter():
            self._available()
            self.attempts.check(key)
            user = self.repository.find_user(data.login.lower())
            try:
                if user is None or not user.ativo:
                    self.passwords.dummy(data.senha)
                    valid = False
                else:
                    valid = self.passwords.verify(user.senha_hash, data.senha)
                if not valid:
                    self.attempts.failure(key)
                    raise invalid_credentials()
                hashed = (
                    self.passwords.hasher.hash(data.senha)
                    if self.passwords.needs_rehash(user.senha_hash)
                    else user.senha_hash
                )
            except Argon2Error:
                raise unavailable() from None
            self._available()
            now = self.clock()
            expires = now + timedelta(hours=8)
            token = "edisk_u_" + secrets.token_urlsafe(32)
            if self.session_changed is not None:
                self.session_changed(None)
            self.repository.issue(user, hashed, token_digest(token), now, expires)
            self.attempts.success(key)
            if self.session_started is not None:
                self.session_started(token)
        return LoginResult(token=token, usuario=PublicUser.model_validate(user), expira_em=expires)

    def current(self, token: str, *, session: Session | None = None) -> SessionView:
        self._available()
        user, expires = self.repository.authenticated(
            token_digest(token),
            self.clock(),
            session=session,
        )
        if session is not None and expires <= self.clock():
            raise invalid_session()
        return SessionView(usuario=PublicUser.model_validate(user), expira_em=expires)

    def logout(self, token: str) -> None:
        self._available()
        digest = token_digest(token)
        if self.session_changed is not None:
            self.session_changed(token)
        self.repository.logout(digest, self.clock())
