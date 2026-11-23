"""Persistência Telegram real, sem rede, polling ou envio."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import test_consentimentos
import test_contatos
from alembic import command
from pydantic import ValidationError
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.auth.errors import AuthError
from app.consentimentos.models import EventoTelegram, IdentidadeTelegram, InscricaoConta
from app.consentimentos.schemas import (
    EscopoEntrada,
    EventoEntrada,
    IdentidadeTelegramEntrada,
    TelegramAceiteEntrada,
    TelegramEventoEntrada,
)
from app.consentimentos.service import (
    AVISO_TELEGRAM_TEXTO,
    AVISO_TELEGRAM_VERSAO,
)
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria, Contato, TelefoneContato
from app.contatos.repository import agora, uuid
from app.persistencia.database import Database
from app.persistencia.errors import SchemaError
from app.persistencia.migrations import bundled_migrations, migrate, migration_config, read_revision

cadastro = test_contatos.cadastro
consentimento = test_consentimentos.consentimento


@pytest.fixture
def telegram(cadastro):
    c, app, token = cadastro
    svc = app.state.consentimentos
    contact_id = uuid()
    with svc.repo.transacao() as s:
        s.add(Contato(id=contact_id, criado_em=agora(), atualizado_em=agora()))
    scope = svc.criar_escopo(EscopoEntrada(operacao_id=uuid4(), nome="Local"), token)
    accounts = []
    for nome in ("Bot A", "Bot B"):
        response = c.post(
            "/contas-envio",
            json={
                "operacao_id": str(uuid4()),
                "nome": nome,
                "plataforma": "telegram",
                "provedor": "telegram_bot_api",
            },
        )
        assert response.status_code == 201, response.text
        accounts.append(response.json()["id"])
    data = IdentidadeTelegramEntrada(
        operacao_id=uuid4(), usuario_externo_id="123456789", chat_externo_id="123456789"
    )
    identity = svc.criar_identidade_telegram(contact_id, data, token)
    return svc, token, contact_id, scope["id"], accounts, identity["id"], data, app


def entrada(f, *, bot="1001", aceite=False, **changes):
    fields = {
        "operacao_id": uuid4(),
        "escopo_id": UUID(f[3]),
        "bot_externo_id": bot,
        "evento_externo_id": uuid(),
        "ocorrido_em": agora(),
    }
    if aceite:
        fields.update(
            categoria="promocoes",
            aviso_versao="1",
            aviso_texto="Aceito receber promoções desta operação neste bot.",
            confirmado=True,
        )
    fields.update(changes)
    return (TelegramAceiteEntrada if aceite else TelegramEventoEntrada)(**fields)


def operar(f, action="inscrever_telegram", index=0, data=None):
    svc, token, _, _, accounts, identity, _, _ = f
    data = data or entrada(f, bot=str(1001 + index), aceite=action == "aceitar_telegram")
    return getattr(svc, action)(accounts[index], identity, data, token)


def avaliar(f, index=0, categoria="promocoes"):
    return f[0].elegibilidade_telegram(f[4][index], f[5], f[3], categoria, f[1])


def test_identidade_sem_telefone_replay_e_sem_uniao_implicita(telegram):
    svc, token, contact, _, _, identity, data, _ = telegram
    assert svc.criar_identidade_telegram(contact, data, token)["id"] == identity
    with svc.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(TelefoneContato)) == 0
        assert s.scalar(select(func.count()).select_from(IdentidadeTelegram)) == 1
        other = Contato(id=uuid(), criado_em=agora(), atualizado_em=agora())
        s.add(other)
        s.flush()
        other_id = other.id
    with pytest.raises(NegocioError):
        svc.criar_identidade_telegram(other_id, data, token)
    with pytest.raises(NegocioError):
        svc.criar_identidade_telegram(
            contact,
            data.model_copy(update={"usuario_externo_id": "987", "chat_externo_id": "987"}),
            token,
        )
    with pytest.raises(ValidationError):
        IdentidadeTelegramEntrada(
            operacao_id=uuid4(), usuario_externo_id="123", chat_externo_id="-123"
        )


def test_inscricao_nao_autoriza_e_aceite_isolado_por_conta_finalidade(telegram):
    f = telegram
    operar(f)
    assert avaliar(f)["estado"] == "desconhecido"
    assert not avaliar(f)["elegivel_consentimento"]
    operar(f, "aceitar_telegram")
    assert avaliar(f)["elegivel_consentimento"]
    assert not avaliar(f, categoria="eventos_grupos")["elegivel_consentimento"]
    assert not avaliar(f, index=1)["elegivel_consentimento"]
    with pytest.raises(NegocioError, match="interação"):
        operar(f, "aceitar_telegram", index=1)
    operar(f, index=1)
    assert not avaliar(f, index=1)["elegivel_consentimento"]
    operar(f, "aceitar_telegram", index=1)
    operar(f, "revogar_telegram")
    assert avaliar(f)["estado"] == "bloqueado"
    assert avaliar(f, index=1)["elegivel_consentimento"]


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmado": False},
        {"confirmado": 1},
        {"aviso_versao": " "},
        {"aviso_texto": " "},
        {"categoria": "qualquer"},
    ],
)
def test_aceite_exige_confirmacao_finalidade_e_aviso(telegram, changes):
    with pytest.raises(ValidationError):
        entrada(telegram, aceite=True, **changes)


def test_aviso_preservado_e_bloqueio_global(telegram):
    f = telegram
    operar(f)
    operar(f, "aceitar_telegram")
    with pytest.raises(NegocioError):
        operar(f, "aceitar_telegram", data=entrada(f, aceite=True, aviso_texto="Outro aviso"))
    with f[0].repo.transacao() as s:
        s.get(Contato, f[2]).bloqueio_global_em = agora()
    assert not avaliar(f)["elegivel_consentimento"]
    with pytest.raises(NegocioError):
        operar(f, "aceitar_telegram")


def test_revogacao_start_inerte_e_reativacao_explicita_persistem(telegram):
    f = telegram
    operar(f)
    aceite = entrada(f, aceite=True)
    accepted = operar(f, "aceitar_telegram", data=aceite)
    operar(
        f,
        "aceitar_telegram",
        data=entrada(
            f,
            aceite=True,
            categoria="eventos_grupos",
            aviso_texto="Aceito eventos e grupos desta operação neste bot.",
        ),
    )
    stop = entrada(f)
    revoked = operar(f, "revogar_telegram", data=stop)
    assert operar(f, "revogar_telegram", data=stop) == revoked
    assert operar(f, "aceitar_telegram", data=aceite) == accepted
    operar(f)  # Outra interação é registrada, sem remover o opt-out.
    assert avaliar(f)["estado"] == "bloqueado"
    with pytest.raises(NegocioError):
        operar(f, "aceitar_telegram")
    reativacao = entrada(f)
    reativada = operar(f, "reativar_telegram", data=reativacao)
    assert operar(f, "reativar_telegram", data=reativacao) == reativada
    assert avaliar(f)["estado"] == "bloqueado"
    operar(
        f,
        "aceitar_telegram",
        data=entrada(
            f,
            aceite=True,
            aviso_versao=AVISO_TELEGRAM_VERSAO,
            aviso_texto=AVISO_TELEGRAM_TEXTO,
        ),
    )
    assert avaliar(f)["elegivel_consentimento"]
    assert not avaliar(f, categoria="eventos_grupos")["elegivel_consentimento"]
    with f[0].repo.database.engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT estado FROM inscricoes_conta").scalar() == "iniciada"
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    # Reabrir conexões ao arquivo, sem estado de consentimento em memória compartilhado.
    reopened = Database(f[7].state.persistence.settings.database_path)
    try:
        fresh = type(f[0])(type(f[0].repo)(reopened), f[0].auth)
        assert fresh.elegibilidade_telegram(f[4][0], f[5], f[3], "promocoes", f[1])[
            "elegivel_consentimento"
        ]
    finally:
        reopened.close()


def test_stop_antes_start_permanece_revogado(telegram):
    operar(telegram, "revogar_telegram")
    operar(telegram)
    assert avaliar(telegram)["estado"] == "bloqueado"


def test_idempotencia_concorrente_e_evento_externo_isolado(telegram):
    f = telegram
    data = entrada(f, evento_externo_id="777")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: operar(f, data=data), range(2)))
    assert results[0] == results[1]
    assert operar(f, data=data.model_copy(update={"operacao_id": uuid4()})) == results[0]
    with pytest.raises(NegocioError):
        operar(f, "revogar_telegram", data=data)
    operar(f, index=1, data=entrada(f, bot="1002", evento_externo_id="777"))
    with f[0].repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(EventoTelegram)) == 2
        assert s.scalar(select(func.count()).select_from(InscricaoConta)) == 2


def test_mesmo_bot_nao_pode_ser_vinculado_a_duas_contas(telegram):
    operar(telegram)
    with pytest.raises(NegocioError):
        operar(telegram, index=1, data=entrada(telegram))
    with pytest.raises(NegocioError):
        operar(telegram, data=entrada(telegram, bot="999"))


def test_falha_commit_nao_deixa_inscricao_evento_ou_bot_parcial(telegram):
    f = telegram

    def falhar(_connection):
        raise OperationalError("commit", {}, Exception("falha controlada"))

    engine = f[0].repo.database.engine
    event.listen(engine, "commit", falhar, once=True)
    try:
        with pytest.raises(NegocioError):
            operar(f)
    finally:
        event.remove(engine, "commit", falhar)
    with f[0].repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(InscricaoConta)) == 0
        assert s.scalar(select(func.count()).select_from(EventoTelegram)) == 0
        assert (
            s.execute(
                text("SELECT telegram_bot_id FROM contas_envio WHERE id=:id"), {"id": f[4][0]}
            ).scalar()
            is None
        )
    operar(f, "revogar_telegram")
    operar(f, "reativar_telegram")
    event.listen(engine, "commit", falhar, once=True)
    try:
        with pytest.raises(NegocioError):
            operar(
                f,
                "aceitar_telegram",
                data=entrada(
                    f,
                    aceite=True,
                    aviso_versao=AVISO_TELEGRAM_VERSAO,
                    aviso_texto=AVISO_TELEGRAM_TEXTO,
                ),
            )
    finally:
        event.remove(engine, "commit", falhar)
    with f[0].repo.transacao() as s:
        assert s.scalar(select(InscricaoConta.estado)) == "revogada"
        assert s.scalar(
            select(func.count())
            .select_from(EventoTelegram)
            .where(EventoTelegram.tipo == "autorizou")
        ) == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE eventos_telegram SET confirmado=0",
        "DELETE FROM eventos_telegram",
        "UPDATE inscricoes_conta SET estado='iniciada'",
        "UPDATE contas_envio SET telegram_bot_id='999' WHERE telegram_bot_id IS NOT NULL",
        "UPDATE identidades_telegram SET chat_externo_id='999'",
    ],
)
def test_integridade_historico_revogacao_e_bot(telegram, sql):
    operar(telegram, "revogar_telegram")
    with pytest.raises(IntegrityError), telegram[0].repo.database.transaction() as s:
        s.execute(text(sql))


def test_datas_invalidas_e_login_obrigatorio(telegram):
    f = telegram
    with pytest.raises(NegocioError):
        operar(f, data=entrada(f, ocorrido_em=datetime.now(UTC) + timedelta(days=1)))
    with pytest.raises(ValidationError):
        entrada(f, ocorrido_em=datetime(2026, 1, 1))
    f[7].state.auth.logout(f[1])
    with pytest.raises(AuthError):
        operar(f)


def test_migracao_upgrade_downgrade_vazio_preserva_dados(cadastro):
    c, app, _ = cadastro
    contact = test_contatos.criar(c).json()
    db = app.state.persistence.database
    with db.transaction() as s:
        before = s.execute(text("SELECT * FROM contatos")).all()
        audit = s.execute(select(Auditoria.id)).all()
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0010_autorizacoes_utc")
        command.upgrade(config, "0011_telegram_optin")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert read_revision(app.state.persistence.settings.database_path) == "0011_telegram_optin"
    with db.transaction() as s:
        assert s.execute(text("SELECT * FROM contatos")).all() == before
        assert s.execute(select(Auditoria.id)).all() == audit
        assert s.get(Contato, contact["id"])
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.downgrade(config, "0015_telegram_envio_unitario")
        command.upgrade(config, "head")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert db.probe("0016_telegram_reativacao")


def test_downgrade_com_historico_recusado(telegram):
    f = telegram
    operar(f)
    operar(f, "aceitar_telegram")
    operar(f, "revogar_telegram")
    operar(f, "reativar_telegram")
    operar(
        f,
        "aceitar_telegram",
        data=entrada(
            f,
            aceite=True,
            aviso_versao=AVISO_TELEGRAM_VERSAO,
            aviso_texto=AVISO_TELEGRAM_TEXTO,
        ),
    )
    with pytest.raises(RuntimeError, match="preserve"), bundled_migrations() as location:
        with f[0].repo.database.engine.begin() as connection:
            config = migration_config(location)
            config.attributes["connection"] = connection
            command.downgrade(config, "0015_telegram_envio_unitario")
    assert read_revision(f[7].state.persistence.settings.database_path) == (
        "0016_telegram_reativacao"
    )


def test_desbloqueio_global_exige_aceite_novo(telegram):
    f = telegram
    operar(f)
    operar(f, "aceitar_telegram")
    for tipo in ("bloqueio_global", "desbloqueio_global"):
        with f[0].repo.transacao() as s:
            version = s.get(Contato, f[2]).consentimento_versao
        f[0].registrar(
            f[2],
            EventoEntrada(
                operacao_id=uuid4(),
                escopo_id=UUID(f[3]),
                versao=version,
                tipo=tipo,
                origem="operador",
                ocorrido_em=agora(),
                justificativa="Pedido registrado",
            ),
            f[1],
        )
        assert not avaliar(f)["elegivel_consentimento"]
    operar(f)  # Interação também não restaura o aceite anterior ao bloqueio global.
    assert not avaliar(f)["elegivel_consentimento"]
    operar(f, "aceitar_telegram")
    assert avaliar(f)["elegivel_consentimento"]


def test_aceite_anterior_interacao_recusado(telegram):
    operar(telegram)
    with pytest.raises(NegocioError):
        operar(
            telegram,
            "aceitar_telegram",
            data=entrada(telegram, aceite=True, ocorrido_em=agora() - timedelta(days=1)),
        )


def test_fk_impede_evento_na_conta_de_outra_inscricao(telegram):
    f = telegram
    recorded = operar(f)
    with f[0].repo.transacao() as s:
        original = s.get(EventoTelegram, recorded["id"])
        values = {col.name: getattr(original, col.name) for col in EventoTelegram.__table__.columns}
    values.update(id=uuid(), conta_envio_id=f[4][1])
    with pytest.raises(IntegrityError), f[0].repo.database.transaction() as s:
        s.add(EventoTelegram(**values))


def test_upgrade_preserva_historico_whatsapp_populado(consentimento):
    _, app, svc, contact, scope, identity, token = consentimento
    svc.registrar(contact["id"], test_consentimentos.entrada(scope, identity, 1), token)
    db = app.state.persistence.database
    tables = (
        "contatos",
        "telefones_contato",
        "identidades_contato",
        "eventos_consentimento",
        "preferencias_contato",
        "auditoria",
    )
    with db.transaction() as s:
        before = {name: s.execute(text("SELECT * FROM " + name)).all() for name in tables}
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.downgrade(config, "0010_autorizacoes_utc")
        command.upgrade(config, "0011_telegram_optin")
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with db.transaction() as s:
        assert {name: s.execute(text("SELECT * FROM " + name)).all() for name in tables} == before
    assert svc.elegibilidade(contact["id"], scope["id"], identity["id"], "promocoes")[
        "elegivel_consentimento"
    ]


def test_falha_migracao_rollback_integral(cadastro):
    _, app, _ = cadastro
    db = app.state.persistence.database
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.downgrade(config, "0010_autorizacoes_utc")
    with db.engine.connect() as conn:
        before = conn.exec_driver_sql("SELECT * FROM sqlite_schema ORDER BY name").all()

    def falhar(_conn, _cursor, sql, *_):
        if sql.startswith("CREATE TABLE eventos_telegram"):
            raise RuntimeError("Falha controlada após DDL")

    event.listen(db.engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(RuntimeError), bundled_migrations() as location:
            with db.engine.begin() as conn:
                config = migration_config(location)
                config.attributes["connection"] = conn
                command.upgrade(config, "0011_telegram_optin")
    finally:
        event.remove(db.engine, "after_cursor_execute", falhar)
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT * FROM sqlite_schema ORDER BY name").all() == before
    assert read_revision(app.state.persistence.settings.database_path) == "0010_autorizacoes_utc"
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.upgrade(config, "0011_telegram_optin")


def test_upgrade_recusa_0010_incompleta_sem_promover_revisao(cadastro):
    _, app, _ = cadastro
    db = app.state.persistence.database
    settings = app.state.persistence.settings
    with bundled_migrations() as location, db.engine.begin() as conn:
        config = migration_config(location)
        config.attributes["connection"] = conn
        command.downgrade(config, "0010_autorizacoes_utc")
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_utc_insert_0010")
    with pytest.raises(SchemaError), bundled_migrations() as location:
        migrate(
            db.engine,
            settings.database_path,
            settings.snapshots_dir,
            "0010_autorizacoes_utc",
            migration_config(location),
            existing=True,
        )
    assert read_revision(settings.database_path) == "0010_autorizacoes_utc"
    with db.engine.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT 1 FROM sqlite_schema WHERE name='eventos_telegram'"
            ).first()
            is None
        )
