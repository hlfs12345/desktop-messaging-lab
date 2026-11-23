"""Autenticação real em SQLite descartável, incluindo falhas de commit."""

import hashlib
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from argon2 import PasswordHasher, extract_parameters
from argon2.low_level import Type
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth.errors import AuthError
from app.auth.models import BOOTSTRAP_ID, EstadoAutenticacao, Sessao, Usuario, utc_text
from app.auth.passwords import COMMON_PASSWORDS
from app.auth.rate_limit import Attempts, CryptoGate
from app.auth.repository import AuthRepository
from app.auth.schemas import BootstrapInput, LoginInput, normalize_password
from app.auth.service import AuthService, token_digest
from app.main import create_app
from app.persistencia.lifecycle import Persistence

# Exclusivamente identidades fictícias. Senhas de produção não são fornecidas pelo aplicativo.
PASSWORD = "frase fictícia de teste 🔒 489!"


def bootstrap_data(**changes):
    return BootstrapInput.model_validate(
        {"nome": "Pessoa de Teste", "login": "Teste.Local", "senha": PASSWORD, **changes}
    )


def login_data(**changes):
    return LoginInput.model_validate({"login": "teste.local", "senha": PASSWORD, **changes})


@pytest.fixture
def auth(settings):
    persistence = Persistence(settings)
    persistence.start()
    service = AuthService(AuthRepository(persistence.database))
    service.initialize()
    try:
        yield service
    finally:
        service.close()
        persistence.close()


@pytest.fixture
def http(settings, authorization):
    app = create_app(settings, authorization=authorization)
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {authorization.token}"}
    ) as client:
        yield client, app.state.auth


def test_bootstrap_once_and_public_response(auth):
    assert auth.setup_needed()
    user = auth.bootstrap(bootstrap_data())
    assert user.perfil == "administrador"
    assert "senha" not in user.model_dump()
    assert not auth.setup_needed()
    with pytest.raises(AuthError) as error:
        auth.bootstrap(bootstrap_data(login="outro.teste"))
    assert error.value.status == 409
    assert auth.current(auth.login(login_data()).token).usuario.id == user.id


def test_bootstrap_real_concurrent_transactions(auth):
    repository = auth.repository
    hashed = auth.passwords.hash(PASSWORD)
    barrier = threading.Barrier(2)

    def create(index):
        barrier.wait(timeout=5)
        try:
            repository.bootstrap("Teste", f"teste{index}", hashed, datetime.now(UTC))
            return 201
        except AuthError as error:
            return error.status

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(create, range(2))) == [201, 409]
    with repository.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Usuario)) == 1
        assert session.get(EstadoAutenticacao, BOOTSTRAP_ID).configurado_em


def test_incomplete_bootstrap_rolls_back_marker(auth):
    with pytest.raises(AuthError):
        auth.repository.bootstrap("", "teste", auth.passwords.hash(PASSWORD), datetime.now(UTC))
    assert auth.setup_needed()
    auth.bootstrap(bootstrap_data())


@pytest.mark.parametrize("action", ["deactivate", "delete"])
def test_bootstrap_never_reopens_after_user_removed_or_inactive(auth, action):
    auth.bootstrap(bootstrap_data())
    with auth.repository.transaction() as session:
        session.execute(delete(Usuario) if action == "delete" else update(Usuario).values(ativo=0))
    assert not auth.setup_needed()
    with pytest.raises(AuthError) as error:
        auth.bootstrap(bootstrap_data())
    assert error.value.status == 409


@pytest.mark.parametrize("inconsistent", [False, True])
def test_marker_absent_or_inconsistent_fails_closed(auth, inconsistent):
    if inconsistent:
        auth.bootstrap(bootstrap_data())
    with auth.repository.transaction() as session:
        if inconsistent:
            session.execute(update(EstadoAutenticacao).values(configurado_em=None))
        else:
            session.execute(delete(EstadoAutenticacao))
    for operation in (auth.setup_needed, auth.initialize, lambda: auth.bootstrap(bootstrap_data())):
        with pytest.raises(AuthError) as error:
            operation()
        assert error.value.status == 503


def test_argon_parameters_salts_and_unicode(auth):
    auth.bootstrap(bootstrap_data(senha="cafe\u0301 com espaços e emoji 🔐"))
    user = auth.repository.find_user("teste.local")
    params = extract_parameters(user.senha_hash)
    assert (
        params.type,
        params.version,
        params.memory_cost,
        params.time_cost,
        params.parallelism,
        params.salt_len,
        params.hash_len,
    ) == (Type.ID, 19, 65536, 3, 4, 16, 32)
    assert auth.login(login_data(senha="café com espaços e emoji 🔐"))
    assert auth.passwords.hash(PASSWORD) != auth.passwords.hash(PASSWORD)


@pytest.mark.parametrize("password", ["curta", "x" * 129, "\ud800" * 15])
def test_password_rejects_size_and_invalid_unicode(password):
    with pytest.raises(ValueError):
        normalize_password(password)


def test_password_counts_codepoints_preserves_case_and_spaces():
    value = "  Frase Com Espaços 🔐 "
    assert normalize_password(value) == value
    assert normalize_password("🔐" * 128) == "🔐" * 128
    assert normalize_password("a\u0301" * 15) == "á" * 15


@pytest.mark.parametrize("password", sorted(COMMON_PASSWORDS))
def test_common_passwords_rejected(auth, password):
    with pytest.raises(AuthError) as error:
        auth.bootstrap(bootstrap_data(senha=password))
    assert error.value.status == 422
    assert auth.setup_needed()


@pytest.mark.parametrize(
    "login,password,inactive",
    [
        ("teste.local", "uma senha incorreta longa", False),
        ("inexistente", PASSWORD, False),
        ("teste.local", PASSWORD, True),
    ],
)
def test_generic_invalid_credentials(auth, login, password, inactive):
    auth.bootstrap(bootstrap_data())
    if inactive:
        with auth.repository.transaction() as session:
            session.execute(update(Usuario).values(ativo=0))
    with pytest.raises(AuthError) as error:
        auth.login(login_data(login=login, senha=password))
    assert (error.value.status, error.value.code, str(error.value)) == (
        401,
        "credenciais_invalidas",
        "Login ou senha inválidos.",
    )


def test_nonexistent_and_inactive_use_dummy(auth, monkeypatch):
    calls = []
    monkeypatch.setattr(auth.passwords, "dummy", lambda _: calls.append(True))
    with pytest.raises(AuthError):
        auth.login(login_data())
    auth.bootstrap(bootstrap_data())
    with auth.repository.transaction() as session:
        session.execute(update(Usuario).values(ativo=0))
    with pytest.raises(AuthError):
        auth.login(login_data())
    assert len(calls) == 2


@pytest.mark.parametrize("stored", ["corrompido", "$argon2id$v=19$m=999999999,t=3,p=4$x$y"])
def test_invalid_hash_cannot_grant_access(auth, stored):
    auth.bootstrap(bootstrap_data())
    with auth.repository.transaction() as session:
        session.execute(update(Usuario).values(senha_hash=stored))
    with pytest.raises(AuthError) as error:
        auth.login(login_data())
    assert error.value.status == 401


def test_rehash_and_session_are_atomic(auth):
    auth.bootstrap(bootstrap_data())
    old_hash = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    with auth.repository.transaction() as session:
        session.execute(update(Usuario).values(senha_hash=old_hash))
    result = auth.login(login_data())
    assert auth.current(result.token)
    assert not auth.passwords.needs_rehash(auth.repository.find_user("teste.local").senha_hash)


def test_only_hash_persisted_and_new_session_replaces_old(auth):
    auth.bootstrap(bootstrap_data())
    first = auth.login(login_data())
    second = auth.login(login_data(login="  TESTE.LOCAL  "))
    with pytest.raises(AuthError):
        auth.current(first.token)
    assert auth.current(second.token).usuario.id == first.usuario.id
    with auth.repository.transaction() as session:
        rows = session.scalars(select(Sessao).order_by(Sessao.criado_em)).all()
        assert len(rows) == 2
        assert rows[0].revogado_em is not None
        assert rows[1].revogado_em is None
        assert rows[1].token_hash == hashlib.sha256(second.token.encode()).hexdigest()
        assert second.expira_em - rows[1].criado_em == timedelta(hours=8)
        assert all(r.token_hash not in {first.token, second.token} for r in rows)


def test_logout_idempotent_and_revoked_persisted(auth):
    auth.bootstrap(bootstrap_data())
    result = auth.login(login_data())
    auth.logout(result.token)
    auth.logout(result.token)
    with pytest.raises(AuthError):
        auth.current(result.token)
    with auth.repository.transaction() as session:
        assert session.scalar(select(Sessao)).revogado_em is not None


def test_absolute_expiry_and_deactivation(auth):
    now = datetime(2026, 10, 29, tzinfo=UTC)
    auth.clock = lambda: now
    auth.bootstrap(bootstrap_data())
    result = auth.login(login_data())
    now += timedelta(hours=8) - timedelta(microseconds=1)
    assert auth.current(result.token)
    now += timedelta(microseconds=1)
    with pytest.raises(AuthError):
        auth.current(result.token)
    result = auth.login(login_data())
    with auth.repository.transaction() as session:
        session.execute(update(Usuario).values(ativo=0))
    with pytest.raises(AuthError):
        auth.current(result.token)


def test_restart_revokes_sessions_before_ready(settings, authorization):
    for iteration in range(2):
        app = create_app(settings, authorization=authorization)
        with TestClient(
            app,
            base_url="http://127.0.0.1",
            headers={"Authorization": f"Bearer {authorization.token}"},
        ) as client:
            if not iteration:
                client.post("/auth/configuracao-inicial", json=bootstrap_data().model_dump())
                token = client.post("/auth/login", json=login_data().model_dump()).json()["token"]
            else:
                assert client.get("/health").status_code == 200
                assert (
                    client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code
                    == 401
                )
                assert client.get("/auth/configuracao-inicial").json() == {"necessaria": False}


def test_rate_limits_progressive_bounded_and_global():
    now = 0.0
    attempts = Attempts(clock=lambda: now, capacity=2)
    for _ in range(5):
        key = attempts.reserve("teste")
        attempts.failure(key)
    for delay in (60, 120, 240, 480, 900):
        with pytest.raises(AuthError) as error:
            attempts.reserve("teste")
        assert error.value.retry_after == delay
        now += delay
        key = attempts.reserve("teste")
        attempts.failure(key)
    attempts.reserve("outro")
    with pytest.raises(AuthError):
        attempts.reserve("terceiro")
    global_limit = Attempts(clock=lambda: 0)
    for index in range(20):
        global_limit.reserve(str(index))
    with pytest.raises(AuthError) as error:
        global_limit.reserve("mais")
    assert error.value.retry_after == 60


def test_crypto_gate_one_running_one_waiter():
    gate = CryptoGate()
    running = threading.Event()
    release = threading.Event()
    waiting = threading.Event()

    def worker():
        with gate.enter():
            running.set()
            release.wait(timeout=5)

    def waiter():
        waiting.set()
        with gate.enter():
            pass

    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(worker)
        assert running.wait(2)
        second = executor.submit(waiter)
        assert waiting.wait(2)
        # O semáforo é inspecionado apenas para sincronizar o teste, sem sleeps.
        deadline = threading.Event()
        for _ in range(100):
            if gate._slots._value == 0:
                break
            deadline.wait(0.01)
        with pytest.raises(AuthError):
            with gate.enter():
                pytest.fail("Terceira operação foi admitida.")
        release.set()
        first.result(3)
        second.result(3)


def test_http_contract_both_credentials_and_no_sensitive_logs(http, caplog):
    client, _ = http
    assert client.get("/auth/configuracao-inicial").json() == {"necessaria": True}
    created = client.post("/auth/configuracao-inicial", json=bootstrap_data().model_dump())
    assert created.status_code == 201
    assert set(created.json()) == {"id", "nome", "login", "perfil"}
    assert client.get("/auth/usuario").status_code == 401
    result = client.post("/auth/login", json=login_data().model_dump())
    assert result.status_code == 200
    token = result.json()["token"]
    credentials = {"X-eDisk-Session": token}
    current = client.get("/auth/usuario", headers=credentials)
    assert current.status_code == 200
    assert set(current.json()) == {"usuario", "expira_em"}
    assert token not in current.text
    assert (
        client.get("/auth/usuario", headers={**credentials, "Authorization": ""}).status_code == 401
    )
    assert client.get("/health").status_code == 200
    assert client.post("/auth/logout", headers=credentials).status_code == 204
    assert client.get("/auth/usuario", headers=credentials).status_code == 401
    for response in (created, result, current):
        assert response.headers["cache-control"] == "no-store"
    assert PASSWORD not in caplog.text and token not in caplog.text
    assert "$argon2" not in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [("perfil", "administrador"), ("senha", "curta"), ("login", "inválido"), ("nome", "")],
)
def test_http_validation_never_echoes_submitted_data(http, field, value):
    client, _ = http
    data = {"nome": "Pessoa de Teste", "login": "Teste.Local", "senha": PASSWORD, field: value}
    result = client.post("/auth/configuracao-inicial", json=data)
    assert result.status_code == 422
    assert PASSWORD not in result.text
    assert "input" not in result.text
    assert "Pessoa de Teste" not in result.text


def test_duplicate_session_headers_and_excessive_payload(http):
    client, _ = http
    token = "edisk_u_" + secrets.token_urlsafe(32)
    assert client.get("/auth/usuario", headers=[("X-eDisk-Session", token)] * 2).status_code == 401
    assert client.get("/auth/usuario", headers={"X-eDisk-Session": token + "x"}).status_code == 401
    assert client.post("/auth/login", content=b"x" * 8193).status_code == 413
    result = client.post("/auth/login", content=iter([b"x" * 4096, b"x" * 4097]))
    assert result.status_code == 413
    assert client.post("/auth/login", content=b'{"senha": "').status_code == 422


@contextmanager
def fail_commit(auth):
    def fail(_connection):
        raise OperationalError("commit fictício", {}, Exception("detalhe privado"))

    engine = auth.repository.database.engine
    event.listen(engine, "commit", fail)
    try:
        yield
    finally:
        event.remove(engine, "commit", fail)


def test_bootstrap_commit_failure_never_reports_success(auth):
    with fail_commit(auth), pytest.raises(AuthError) as error:
        auth.bootstrap(bootstrap_data())
    assert error.value.status == 503
    assert auth.setup_needed()


def test_login_commit_failure_never_emits_token_or_changes_hash(auth):
    auth.bootstrap(bootstrap_data())
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    with auth.repository.transaction() as session:
        session.execute(update(Usuario).values(senha_hash=old))
    # Falha somente na transação de emissão, após a consulta das credenciais.
    original = auth.repository.issue

    def issue(*args):
        with fail_commit(auth):
            return original(*args)

    auth.repository.issue = issue
    with pytest.raises(AuthError):
        auth.login(login_data())
    assert auth.repository.find_user("teste.local").senha_hash == old
    with auth.repository.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Sessao)) == 0


def test_logout_commit_failure_is_not_success(auth):
    auth.bootstrap(bootstrap_data())
    result = auth.login(login_data())
    with fail_commit(auth), pytest.raises(AuthError) as error:
        auth.logout(result.token)
    assert error.value.status == 503
    assert auth.current(result.token)


def test_constraints_login_unique_and_session_fk(auth):
    auth.bootstrap(bootstrap_data())
    user = auth.repository.find_user("teste.local")
    values = {column.name: getattr(user, column.name) for column in Usuario.__table__.columns}
    values["id"] = str(uuid4())
    with pytest.raises(IntegrityError), auth.repository.database.transaction() as session:
        session.add(Usuario(**values))
    with pytest.raises(IntegrityError), auth.repository.database.transaction() as session:
        session.add(
            Sessao(
                id=str(uuid4()),
                usuario_id=str(uuid4()),
                token_hash="0" * 64,
                criado_em=user.criado_em,
                expira_em=user.criado_em + timedelta(hours=8),
            )
        )
    assert utc_text(user.criado_em).endswith("Z")


def test_session_cannot_be_used_after_service_closed(auth):
    auth.close()
    with pytest.raises(AuthError):
        auth.login(login_data())


def test_token_format():
    for token in ("", "x" * 8192, "edisk_u_" + "á" * 43):
        with pytest.raises(AuthError):
            token_digest(token)


def test_http_rate_limit_before_hashing(http, monkeypatch):
    client, service = http
    calls = []
    monkeypatch.setattr(service.passwords, "dummy", lambda _: calls.append(True))
    for _ in range(5):
        assert client.post("/auth/login", json=login_data().model_dump()).status_code == 401
    limited = client.post("/auth/login", json=login_data().model_dump())
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 59
    assert limited.headers["cache-control"] == "no-store"
    assert len(calls) == 5


def test_queued_attempt_rechecks_new_block():
    attempts = Attempts(clock=lambda: 0)
    key = attempts.reserve("teste")
    for _ in range(5):
        attempts.failure(key)
    with pytest.raises(AuthError) as error:
        attempts.check(key)
    assert error.value.status == 429


def test_http_commit_failure_and_unexpected_error_sanitized(http, monkeypatch, caplog):
    client, service = http
    with fail_commit(service):
        result = client.post("/auth/configuracao-inicial", json=bootstrap_data().model_dump())
    assert result.status_code == 503
    assert PASSWORD not in result.text and "commit fictício" not in result.text

    def fail():
        raise RuntimeError(PASSWORD)

    monkeypatch.setattr(service, "setup_needed", fail)
    result = client.get("/auth/configuracao-inicial")
    assert result.status_code == 500
    assert PASSWORD not in result.text and PASSWORD not in caplog.text


def test_instance_is_checked_before_reading_auth_body(http):
    client, _ = http
    assert (
        client.post("/auth/login", content=b"x" * 9000, headers={"Authorization": ""}).status_code
        == 401
    )


def test_restart_rejects_missing_marker_and_releases_lock(settings, authorization):
    persistence = Persistence(settings)
    persistence.start()
    with persistence.database.transaction() as session:
        session.execute(delete(EstadoAutenticacao))
    persistence.close()
    with pytest.raises(RuntimeError), TestClient(create_app(settings, authorization=authorization)):
        pass
    persistence.start()  # O lifespan falho liberou o lock; não recriou o marcador.
    try:
        with persistence.database.transaction() as session:
            assert session.scalar(select(func.count()).select_from(EstadoAutenticacao)) == 0
    finally:
        persistence.close()


def test_user_deactivated_during_password_verification_does_not_get_session(auth, monkeypatch):
    auth.bootstrap(bootstrap_data())
    verify = auth.passwords.verify

    def verify_and_deactivate(stored, password):
        result = verify(stored, password)
        with auth.repository.transaction() as session:
            session.execute(update(Usuario).values(ativo=0))
        return result

    monkeypatch.setattr(auth.passwords, "verify", verify_and_deactivate)
    with pytest.raises(AuthError) as error:
        auth.login(login_data())
    assert error.value.status == 401
