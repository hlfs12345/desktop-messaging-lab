"""Regras reais em SQLite descartável; sem provedores."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import test_contatos
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from test_contatos import criar

from app.consentimentos.models import EventoConsentimento, PreferenciaContato
from app.consentimentos.schemas import EscopoEntrada, EventoEntrada, IdentidadeEntrada
from app.consentimentos.service import ConsentimentosService
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria

cadastro = test_contatos.cadastro


@pytest.fixture
def consentimento(cadastro):
    c, app, token = cadastro
    contact = criar(c).json()
    service = ConsentimentosService(app.state.contatos.repo, app.state.auth)
    scope = service.criar_escopo(
        EscopoEntrada(operacao_id=uuid4(), nome="Comunicação da operação local"),
        token,
    )
    identity = service.criar_identidade(
        contact["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(contact["telefones"][0]["id"]),
            canal="whatsapp",
            versao=0,
        ),
        token,
    )
    return c, app, service, contact, scope, identity, token


def entrada(scope, identity, version, tipo="autorizou", categoria="promocoes", **kwargs):

    fields = dict(
        operacao_id=uuid4(),
        escopo_id=UUID(scope["id"]),
        versao=version,
        tipo=tipo,
        identidade_contato_id=UUID(identity["id"]) if identity else None,
        categoria=categoria,
        origem="operador",
        ocorrido_em=datetime.now(UTC),
        metodo_captura="Formulário assinado",
        evidencia_referencia="arquivo-local:prova-001",
        justificativa="Pedido explícito registrado",
    )
    fields.update(kwargs)
    return EventoEntrada(**fields)


def registrar(f, tipo="autorizou", categoria="promocoes", identity=True, **kwargs):
    _, _, svc, contact, scope, ident, token = f
    version = svc.estado(contact["id"], scope["id"])["versao"]
    data = entrada(scope, ident if identity else None, version, tipo, categoria, **kwargs)
    return svc.registrar(contact["id"], data, token)


def avaliar(f, categoria="promocoes"):
    _, _, svc, contact, scope, ident, _ = f
    return svc.elegibilidade(contact["id"], scope["id"], ident["id"], categoria)


def test_desconhecido_autorizacao_revogacao_e_categorias(consentimento):
    f = consentimento
    assert avaliar(f)["elegivel_consentimento"] is False
    registrar(f)
    assert avaliar(f)["elegivel_consentimento"] is True
    assert avaliar(f, "eventos_grupos")["estado"] == "desconhecido"
    assert avaliar(f)["existencia_canal"] == "nao_verificada"
    assert avaliar(f)["capacidade_envio"] == "nao_avaliada"
    registrar(f, "revogou")
    assert avaliar(f)["estado"] == "bloqueado"
    assert avaliar(f)["motivos"] == ["categoria_revogada"]
    registrar(f)
    assert avaliar(f)["elegivel_consentimento"] is True


@pytest.mark.parametrize("global_", [False, True])
def test_bloqueio_desbloqueio_nao_restaura_e_preserva_revogacao(consentimento, global_):
    f = consentimento
    registrar(f)
    registrar(f, "revogou", "eventos_grupos")
    suffix = "global" if global_ else "canal"
    registrar(f, "bloqueio_" + suffix, None, identity=not global_)
    assert avaliar(f)["estado"] == "bloqueado"
    with pytest.raises(NegocioError) as ex:
        registrar(f)
    assert ex.value.codigo == "bloqueado"
    registrar(f, "desbloqueio_" + suffix, None, identity=not global_)
    assert avaliar(f)["estado"] == "desconhecido"
    assert avaliar(f, "eventos_grupos")["estado"] == "bloqueado"
    registrar(f)
    assert avaliar(f)["elegivel_consentimento"]


def test_precedencia_global_canal_e_duas_identidades(consentimento):
    f = consentimento
    c, _, svc, contact, scope, ident, token = f
    phone = c.post(
        f"/contatos/{contact['id']}/telefones", json={"telefone": "+442083661177"}
    ).json()

    other = svc.criar_identidade(
        contact["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(phone["id"]),
            canal="whatsapp",
            versao=1,
        ),
        token,
    )
    registrar(f)
    assert not svc.elegibilidade(
        contact["id"],
        scope["id"],
        other["id"],
        "promocoes",
    )["elegivel_consentimento"]
    registrar(f, "bloqueio_canal", None)
    registrar(f, "bloqueio_global", None, identity=False)
    assert avaliar(f)["motivos"][:2] == ["bloqueio_global", "bloqueio_canal"]
    assert (
        svc.elegibilidade(
            contact["id"],
            scope["id"],
            other["id"],
            "promocoes",
        )["motivos"][0]
        == "bloqueio_global"
    )


def test_idempotencia_concorrencia_e_reutilizacao_divergente(consentimento):
    _, app, svc, contact, scope, ident, token = consentimento
    data = entrada(scope, ident, 1)
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: svc.registrar(contact["id"], data, token), range(2)))
    assert results[0] == results[1]
    assert svc.estado(contact["id"], scope["id"])["versao"] == 2
    with pytest.raises(NegocioError) as ex:
        svc.registrar(contact["id"], data.model_copy(update={"categoria": "eventos_grupos"}), token)
    assert ex.value.status == 409
    a, b = entrada(scope, ident, 2), entrada(scope, ident, 2, categoria="eventos_grupos")

    def run(data):
        try:
            svc.registrar(contact["id"], data, token)
            return 200
        except NegocioError as ex:
            return ex.status

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(run, [a, b])) == [200, 409]
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(EventoConsentimento)) == 2


def test_falha_commit_rollback_completo(consentimento):
    f = consentimento
    _, app, svc, contact, scope, ident, token = f
    engine = app.state.persistence.database.engine
    with svc.repo.transacao() as s:
        audits = s.scalar(select(func.count()).select_from(Auditoria))

    def fail(_):
        raise OperationalError("sql privado", {}, Exception("evidencia privada"))

    event.listen(engine, "commit", fail)
    try:
        with pytest.raises(NegocioError) as ex:
            svc.registrar(contact["id"], entrada(scope, ident, 1), token)
        assert ex.value.status == 503 and "privad" not in str(ex.value)
    finally:
        event.remove(engine, "commit", fail)
    assert svc.estado(contact["id"], scope["id"])["versao"] == 1
    assert avaliar(f)["estado"] == "desconhecido"
    with svc.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(EventoConsentimento)) == 0
        assert s.scalar(select(func.count()).select_from(PreferenciaContato)) == 0
        assert s.scalar(select(func.count()).select_from(Auditoria)) == audits


def test_evidencia_obrigatoria_datas_e_escopo_inexistente(consentimento):
    from pydantic import ValidationError

    _, _, svc, contact, scope, ident, token = consentimento
    for changes in (
        {"metodo_captura": None},
        {"evidencia_referencia": " "},
        {"ocorrido_em": datetime.now()},
        {"origem": "importacao"},
        {"identidade_contato_id": None},
        {"categoria": None},
        {"tipo": "revogou", "justificativa": None},
    ):
        with pytest.raises(ValidationError):
            entrada(scope, ident, 1, **changes)
    with pytest.raises(NegocioError):
        svc.registrar(
            contact["id"],
            entrada(
                scope,
                ident,
                1,
                ocorrido_em=datetime.now(UTC) + timedelta(days=1),
            ),
            token,
        )
    with pytest.raises(NegocioError) as ex:
        svc.registrar(contact["id"], entrada(scope, ident, 1, escopo_id=uuid4()), token)
    assert ex.value.status == 404


def test_historico_paginado_imutavel_e_auditoria_sem_evidencia(consentimento):
    f = consentimento
    _, _, svc, contact, scope, _, _ = f
    registrar(f)
    registrar(f, "revogou")
    page = svc.historico(contact["id"], scope["id"], 1, 1)
    assert page["total"] == 2 and page["itens"][0]["tipo"] == "revogou"
    assert svc.historico(contact["id"], scope["id"], 2, 1)["itens"][0]["tipo"] == "autorizou"
    for sql in (
        "UPDATE eventos_consentimento SET origem='operador'",
        "DELETE FROM eventos_consentimento",
    ):
        with pytest.raises(NegocioError):
            with svc.repo.transacao() as s:
                s.execute(text(sql))
    with svc.repo.transacao() as s:
        audit = str(s.scalars(select(Auditoria.resumo_json)).all())
        assert "prova-001" not in audit and "assinado" not in audit


def test_escopo_explicito_unico_e_identidade_idempotente(consentimento):

    _, _, svc, contact, scope, ident, token = consentimento
    assert (
        svc.criar_escopo(
            EscopoEntrada(operacao_id=UUID(scope["id"]), nome=scope["nome"]),
            token,
        )
        == scope
    )
    with pytest.raises(NegocioError):
        svc.criar_escopo(EscopoEntrada(operacao_id=uuid4(), nome="Outra marca"), token)
    data = IdentidadeEntrada(
        operacao_id=UUID(ident["id"]),
        telefone_id=UUID(ident["telefone_id"]),
        canal="whatsapp",
        versao=0,
    )
    assert svc.criar_identidade(contact["id"], data, token) == ident


@pytest.mark.parametrize("phase", ["eventos_consentimento", "preferencias_contato", "auditoria"])
def test_falha_de_escrita_nao_deixa_projecao_parcial(consentimento, phase):
    _, app, svc, contact, scope, identity, token = consentimento
    engine = app.state.persistence.database.engine

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO " + phase):
            raise OperationalError("sql privado", {}, Exception("evidencia privada"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(NegocioError) as ex:
            svc.registrar(contact["id"], entrada(scope, identity, 1), token)
        assert ex.value.status == 503
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert svc.estado(contact["id"], scope["id"])["versao"] == 1
    assert avaliar(consentimento)["estado"] == "desconhecido"
    assert svc.historico(contact["id"], scope["id"])["total"] == 0


def test_reabertura_preserva_bloqueios_e_historico(consentimento):
    from pathlib import Path

    from app.contatos.repository import CadastroRepository
    from app.persistencia.database import Database

    registrar(consentimento)
    registrar(consentimento, "bloqueio_global", None, identity=False)
    _, app, _, contact, scope, identity, _ = consentimento
    with app.state.persistence.database.transaction() as s:
        database_path = s.execute(text("PRAGMA database_list")).all()[0][2]
    reopened = Database(Path(database_path))
    try:
        fresh = ConsentimentosService(CadastroRepository(reopened), app.state.auth)
        result = fresh.elegibilidade(
            contact["id"],
            scope["id"],
            identity["id"],
            "promocoes",
        )
        assert result["motivos"][0] == "bloqueio_global"
        assert fresh.historico(contact["id"], scope["id"])["total"] == 2
    finally:
        reopened.close()


def test_downgrade_recusa_perder_historico(consentimento):
    from alembic import command

    from app.persistencia.migrations import bundled_migrations, migration_config

    registrar(consentimento)
    _, app, _, _, _, _, _ = consentimento
    with bundled_migrations() as location:
        config = migration_config(location)
        with pytest.raises(RuntimeError, match="historico"):
            with app.state.persistence.database.engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0002_contatos_listas_importacoes")
    assert avaliar(consentimento)["elegivel_consentimento"]


def test_constraints_de_evento_rejeitam_ausencia_de_evidencia_e_alvo(consentimento):
    registrar(consentimento)
    _, _, svc, _, _, _, _ = consentimento
    for changed, value in (
        ("evidencia_referencia", "NULL"),
        ("categoria", "NULL"),
        ("origem", "'telegram'"),
        ("metodo_captura", "NULL"),
    ):
        with pytest.raises(NegocioError):
            with svc.repo.transacao() as s:
                columns = [
                    row[1]
                    for row in s.execute(text("PRAGMA table_info(eventos_consentimento)")).all()
                ]
                values = [
                    ":id" if col == "id" else value if col == changed else col for col in columns
                ]
                s.execute(
                    text(
                        "INSERT INTO eventos_consentimento (" + ",".join(columns) + ") "
                        "SELECT " + ",".join(values) + " FROM eventos_consentimento LIMIT 1"
                    ),
                    {"id": str(uuid4())},
                )


def test_repetir_autorizacao_antiga_nao_remove_revogacao(consentimento):
    _, _, svc, contact, scope, identity, token = consentimento
    old = entrada(scope, identity, 1)
    first = svc.registrar(contact["id"], old, token)
    registrar(consentimento, "revogou")
    version = svc.estado(contact["id"], scope["id"])["versao"]
    assert svc.registrar(contact["id"], old, token) == first
    assert avaliar(consentimento)["estado"] == "bloqueado"
    assert svc.estado(contact["id"], scope["id"])["versao"] == version


def test_replay_identidade_legada_sem_hash_nao_presume_versao(consentimento):
    _, _, svc, contact, _, identity, token = consentimento
    with svc.repo.transacao() as session:
        # Simula somente nesta base descartavel uma auditoria anterior a 0007.
        trigger = session.scalar(
            text("SELECT sql FROM sqlite_master WHERE name='auditoria_sem_update'")
        )
        session.execute(text("DROP TRIGGER auditoria_sem_update"))
        session.execute(
            text(
                "UPDATE auditoria SET resumo_json='{}' "
                "WHERE entidade='identidade_contato' AND entidade_id=:id"
            ),
            {"id": identity["id"]},
        )
        session.execute(text(trigger))
    data = IdentidadeEntrada(
        operacao_id=UUID(identity["id"]),
        telefone_id=UUID(identity["telefone_id"]),
        canal="whatsapp",
        versao=0,
    )
    with pytest.raises(NegocioError) as ex:
        svc.criar_identidade(contact["id"], data, token)
    assert ex.value.status == 409
    assert svc.identidades(contact["id"])["itens"] == [identity]


def test_expiracao_durante_leitura_da_sessao_reverte_mutacao(consentimento, monkeypatch):
    from app.auth.errors import AuthError

    _, app, svc, contact, scope, identity, token = consentimento
    original = app.state.auth.repository.authenticated
    expiry = app.state.auth.current(token).expira_em

    def expire_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("session") is not None:
            monkeypatch.setattr(app.state.auth, "clock", lambda: expiry)
        return result

    monkeypatch.setattr(app.state.auth.repository, "authenticated", expire_after_read)
    with svc.repo.transacao() as session:
        before = session.scalar(select(func.count()).select_from(Auditoria))
    with pytest.raises(AuthError) as ex:
        svc.registrar(contact["id"], entrada(scope, identity, 1), token)
    assert ex.value.status == 401
    assert svc.estado(contact["id"], scope["id"])["versao"] == 1
    assert svc.historico(contact["id"], scope["id"])["total"] == 0
    assert avaliar(consentimento)["estado"] == "desconhecido"
    with svc.repo.transacao() as session:
        assert session.scalar(select(func.count()).select_from(Auditoria)) == before
