"""Envio Telegram unitario; transporte sempre controlado e sem rede."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
import test_telegram_persistencia as telegram_tests
import test_templates
from alembic import command
from helpers.runtime_lifetime import SimulatedLifetime
from sqlalchemy import func, select, text

from app.campanhas.execucoes_models import ExecucaoCampanha, ItemExecucao, TentativaEnvio
from app.campanhas.executor_mock import ContextoOperacionalMock
from app.campanhas.executor_telegram import ExecutorTelegram
from app.campanhas.models import (
    AutorizacaoEnvioTelegram,
    PreparacaoItemTelegram,
    TentativaEnvioTelegram,
)
from app.consentimentos.schemas import IdentidadeTelegramEntrada, TelegramEventoEntrada
from app.contatos.errors import NegocioError
from app.contatos.models import Contato
from app.contatos.repository import agora, uuid
from app.persistencia.migrations import bundled_migrations, migration_config, read_revision
from app.provedores.telegram import ResultadoEnvioTelegram, Telegram

pytestmark = pytest.mark.skipif(os.name != "nt", reason="DPAPI real")
cadastro = telegram_tests.cadastro
telegram = telegram_tests.telegram


class ProvedorControlado:
    resultado = ResultadoEnvioTelegram("confirmada", "aceita", message_id=321)
    chamadas = 0

    def enviar(self, token, chat_id, texto):
        type(self).chamadas += 1
        assert token == "123456:token_teste"
        assert chat_id == "123456789"
        assert texto == "Mensagem Telegram final"
        return type(self).resultado

    def cancelar(self):
        pass


@pytest.fixture
def pronta(telegram, cadastro):
    f = telegram
    c, app, _ = cadastro
    telegram_tests.operar(f)
    telegram_tests.operar(f, "aceitar_telegram")
    account = c.get("/contas-envio/" + f[4][0]).json()
    stored = c.put(
        "/contas-envio/" + f[4][0] + "/credencial",
        json={
            "operacao_id": str(uuid4()),
            "versao": account["versao"],
            "credencial": "123456:token_teste",
        },
    )
    assert stored.status_code == 200, stored.text
    template = test_templates.criar(
        c,
        plataforma="telegram",
        conteudo="Mensagem Telegram final",
    )
    lista = c.post("/listas", json={"nome": "Telegram unitario"}).json()
    assert c.put(f"/listas/{lista['id']}/contatos/{f[2]}").status_code == 204
    campaign = c.post(
        "/campanhas",
        json={
            "operacao_id": str(uuid4()),
            "nome": "Envio Telegram unitario",
            "conta_envio_id": f[4][0],
            "escopo_id": f[3],
            "template_versao_id": template["template_versao_id"],
            "categoria": "promocoes",
            "listas": [lista["id"]],
        },
    )
    assert campaign.status_code == 201, campaign.text
    item = campaign.json()
    selected = c.put(
        f"/campanhas/{item['id']}/selecoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": item["versao"],
            "identidade_telegram_id": f[5],
            "selecionada": True,
        },
    )
    assert selected.status_code == 200, selected.text
    initialized = c.post(
        "/campanhas/chaves/inicializar", json={"operacao_id": str(uuid4())}
    )
    assert initialized.status_code == 200, initialized.text
    assert (
        c.post(
            "/campanhas/chaves/conferir",
            json={
                "operacao_id": str(uuid4()),
                "codigo": initialized.json()["codigo_recuperacao"],
            },
        ).status_code
        == 200
    )
    root = f"/campanhas/{item['id']}"
    review = c.get(root + "/revisao-preparacao").json()
    prepared = c.post(
        root + "/preparacoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": review["versao"],
            "marcador": review["marcador"],
        },
    )
    assert prepared.status_code == 201, prepared.text
    prep = prepared.json()
    snapshot = c.get(root + f"/preparacoes/{prep['preparacao_id']}").json()
    confirmed = c.post(
        root + f"/preparacoes/{prep['preparacao_id']}/confirmacao",
        json={
            "operacao_id": str(uuid4()),
            "versao": prep["versao"],
            "preparacao_id": prep["preparacao_id"],
            "impressao": snapshot["impressao"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return c, app, f, root, prep["preparacao_id"]


def _nova_identidade(f, *, final, conta=0, consentir=True):
    svc, token = f[0], f[1]
    contato = uuid()
    with svc.repo.transacao() as session:
        session.add(Contato(id=contato, criado_em=agora(), atualizado_em=agora()))
    identidade = svc.criar_identidade_telegram(
        contato,
        IdentidadeTelegramEntrada(
            operacao_id=uuid4(),
            usuario_externo_id=f"9876543{final}",
            chat_externo_id=f"9876543{final}",
        ),
        token,
    )["id"]
    svc.inscrever_telegram(
        f[4][conta],
        identidade,
        telegram_tests.entrada(f, bot=str(1001 + conta)),
        token,
    )
    if consentir:
        svc.aceitar_telegram(
            f[4][conta],
            identidade,
            telegram_tests.entrada(f, bot=str(1001 + conta), aceite=True),
            token,
        )
    return contato, identidade


@pytest.fixture
def lote_rascunho(telegram, cadastro):
    f = telegram
    c, app, _ = cadastro
    telegram_tests.operar(f)
    telegram_tests.operar(f, "aceitar_telegram")
    account = c.get("/contas-envio/" + f[4][0]).json()
    stored = c.put(
        "/contas-envio/" + f[4][0] + "/credencial",
        json={
            "operacao_id": str(uuid4()),
            "versao": account["versao"],
            "credencial": "123456:token_teste",
        },
    )
    assert stored.status_code == 200, stored.text
    autorizado = _nova_identidade(f, final="21")
    sem_consentimento = _nova_identidade(f, final="22", consentir=False)
    outro_bot = _nova_identidade(f, final="23", conta=1)
    template = test_templates.criar(c, plataforma="telegram", conteudo="Mensagem em lote")
    lista = c.post("/listas", json={"nome": "Telegram lote"}).json()
    for contato in (f[2], autorizado[0], sem_consentimento[0], outro_bot[0]):
        assert c.put(f"/listas/{lista['id']}/contatos/{contato}").status_code == 204
    campaign = c.post(
        "/campanhas",
        json={
            "operacao_id": str(uuid4()),
            "nome": "Preparacao Telegram lote",
            "conta_envio_id": f[4][0],
            "escopo_id": f[3],
            "template_versao_id": template["template_versao_id"],
            "categoria": "promocoes",
            "listas": [lista["id"]],
        },
    ).json()
    root = f"/campanhas/{campaign['id']}"
    version = campaign["versao"]
    for identidade in (f[5], autorizado[1], sem_consentimento[1]):
        selected = c.put(
            root + "/selecoes",
            json={
                "operacao_id": str(uuid4()),
                "versao": version,
                "identidade_telegram_id": identidade,
                "selecionada": True,
            },
        )
        assert selected.status_code == 200, selected.text
        version = selected.json()["versao"]
    return {
        "cliente": c,
        "app": app,
        "telegram": f,
        "root": root,
        "versao": version,
        "incluidos": sorted((f[5], autorizado[1])),
        "sem_consentimento": sem_consentimento[1],
        "outro_bot": outro_bot[1],
    }


def _preparar_lote(f):
    c, root = f["cliente"], f["root"]
    initialized = c.post(
        "/campanhas/chaves/inicializar", json={"operacao_id": str(uuid4())}
    )
    assert initialized.status_code == 200, initialized.text
    assert c.post(
        "/campanhas/chaves/conferir",
        json={
            "operacao_id": str(uuid4()),
            "codigo": initialized.json()["codigo_recuperacao"],
        },
    ).status_code == 200
    review = c.get(root + "/revisao-preparacao")
    assert review.status_code == 200, review.text
    prepared = c.post(
        root + "/preparacoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": review.json()["versao"],
            "marcador": review.json()["marcador"],
        },
    )
    assert prepared.status_code == 201, prepared.text
    prep = prepared.json()
    snapshot = c.get(root + f"/preparacoes/{prep['preparacao_id']}")
    assert snapshot.status_code == 200, snapshot.text
    confirmed = c.post(
        root + f"/preparacoes/{prep['preparacao_id']}/confirmacao",
        json={
            "operacao_id": str(uuid4()),
            "versao": prep["versao"],
            "preparacao_id": prep["preparacao_id"],
            "impressao": snapshot.json()["impressao"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return prep["preparacao_id"], review.json(), snapshot.json(), confirmed.json()


def _iniciar_execucao_lote(f):
    prep, _, _, _ = _preparar_lote(f)
    app, original = f["app"], f["telegram"]
    campanha_id = f["root"].rsplit("/", 1)[-1]
    revisao = app.state.execucoes.revisao(campanha_id, prep, original[1])
    entrada = {
        "operacao_id": str(uuid4()),
        "versao": revisao["versao"],
        "modo": "telegram",
        "impressao": revisao["impressao"],
        "resumo_configuracao": revisao["resumo_configuracao"],
        "marcador_inicio": revisao["marcador_inicio"],
        "confirmo_inicio": True,
    }
    recibo = app.state.execucoes.iniciar(campanha_id, prep, entrada, original[1])
    return prep, recibo


def _executor_lote(f, recibo, *, retomar=False):
    app, original = f["app"], f["telegram"]
    contexto = ContextoOperacionalMock(
        app.state.execucoes,
        app.state.persistence,
        app.state.execucoes.p.abertura,
        lifetime=SimulatedLifetime(),
    )
    executor = ExecutorTelegram(
        app.state.execucoes,
        app.state.envios_telegram,
        app.state.telegram_recepcao,
        contexto,
    )
    with app.state.contatos.repo.transacao() as session:
        versao = session.get(ExecucaoCampanha, recibo["execucao_id"]).versao
    admissao = executor.admitir(
        recibo["execucao_id"], original[1], versao, retomar=retomar
    )
    return contexto, executor, admissao


def confirmar(f, operacao=None):
    c, app, _, root, prep = f
    review = c.get(root + f"/preparacoes/{prep}/revisao-envio-telegram")
    assert review.status_code == 200, review.text
    body = {
        "operacao_id": operacao or str(uuid4()),
        "versao": review.json()["versao"],
        "impressao": review.json()["impressao"],
        "marcador_envio": review.json()["marcador_envio"],
        "confirmo_envio_real": True,
    }
    return c.post(root + f"/preparacoes/{prep}/envio-telegram", json=body), body


def test_preparacao_lote_cifrada_integra_deterministica_e_sem_duplicidades(
    lote_rascunho,
):
    prep, review, snapshot, _ = _preparar_lote(lote_rascunho)
    assert (review["incluidos"], review["excluidos"], review["total"]) == (2, 1, 3)
    assert (snapshot["incluidos"], snapshot["excluidos"], snapshot["total"]) == (2, 1, 3)
    identities = [row["identidade_telegram_id"] for row in snapshot["itens"]]
    assert identities == sorted(identities)
    assert len(identities) == len(set(identities)) == 3
    included = sorted(
        row["identidade_telegram_id"]
        for row in snapshot["itens"]
        if row["incluido"]
    )
    assert included == lote_rascunho["incluidos"]
    excluded = next(row for row in snapshot["itens"] if not row["incluido"])
    assert excluded["identidade_telegram_id"] == lote_rascunho["sem_consentimento"]
    assert "consentimento_telegram_ausente" in excluded["motivos"]
    c, root = lote_rascunho["cliente"], lote_rascunho["root"]
    for identity in included:
        content = c.get(root + f"/preparacoes/{prep}/itens/{identity}/conteudo")
        assert content.status_code == 200, content.text
        assert content.json()["conteudo_renderizado"] == "Mensagem em lote"
    assert c.get(
        root + f"/preparacoes/{prep}/itens/{excluded['identidade_telegram_id']}/conteudo"
    ).status_code == 409
    assert c.get(root + f"/preparacoes/{prep}").json()["integridade"] == "verificada"
    with lote_rascunho["app"].state.contatos.repo.transacao() as session:
        rows = session.scalars(
            select(PreparacaoItemTelegram)
            .where(PreparacaoItemTelegram.preparacao_id == prep)
            .order_by(PreparacaoItemTelegram.identidade_telegram_id)
        ).all()
        assert len(rows) == 3
        assert all(
            row.conteudo_cifrado != b"Mensagem em lote" for row in rows if row.incluido
        )


def test_consentimento_bot_e_envio_real_de_lote_sao_bloqueados(lote_rascunho):
    c, root = lote_rascunho["cliente"], lote_rascunho["root"]
    wrong = c.put(
        root + "/selecoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": lote_rascunho["versao"],
            "identidade_telegram_id": lote_rascunho["outro_bot"],
            "selecionada": True,
        },
    )
    assert wrong.status_code == 409
    assert wrong.json()["codigo"] == "inscricao_telegram_invalida"
    bulk = c.put(
        root + "/selecoes-elegiveis",
        json={"operacao_id": str(uuid4()), "versao": lote_rascunho["versao"]},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["selecionados"] == 2
    restored = c.put(
        root + "/selecoes",
        json={
            "operacao_id": str(uuid4()),
            "versao": bulk.json()["versao"],
            "identidade_telegram_id": lote_rascunho["sem_consentimento"],
            "selecionada": True,
        },
    )
    assert restored.status_code == 200, restored.text
    prep, review, snapshot, confirmed = _preparar_lote(lote_rascunho)
    excluded = next(row for row in review["itens"] if not row["incluido"])
    assert excluded["identidade_telegram_id"] == lote_rascunho["sem_consentimento"]
    ProvedorControlado.chamadas = 0
    lote_rascunho["app"].state.envios_telegram.provedor_factory = ProvedorControlado
    response = c.post(
        root + f"/preparacoes/{prep}/envio-telegram",
        json={
            "operacao_id": str(uuid4()),
            "versao": confirmed["versao"],
            "impressao": snapshot["impressao"],
            "marcador_envio": "envio-telegram-v1." + "0" * 64,
            "confirmo_envio_real": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["codigo"] == "envio_telegram_lote_indisponivel"
    assert ProvedorControlado.chamadas == 0


def test_migracao_0017_preserva_fotografia_autorizacao_e_integridade(pronta):
    ProvedorControlado.chamadas = 0
    pronta[1].state.envios_telegram.provedor_factory = ProvedorControlado
    response, _ = confirmar(pronta)
    assert response.status_code == 201, response.text
    app = pronta[1]
    db = app.state.persistence.database
    tables = (
        "campanha_selecoes_telegram",
        "campanha_preparacao_itens_telegram",
        "campanha_autorizacoes_envio_telegram",
        "campanha_tentativas_envio_telegram",
    )
    with db.engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in tables
        }
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0016_telegram_reativacao")
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.upgrade(config, "0018_telegram_executor_lote")
        after = {
            table: connection.execute(text(f"SELECT * FROM {table}")).mappings().all()
            for table in tables
        }
        assert after == before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA integrity_check").all() == [("ok",)]
    assert read_revision(app.state.persistence.settings.database_path) == (
        "0018_telegram_executor_lote"
    )
    assert pronta[0].get(
        pronta[3] + f"/preparacoes/{pronta[4]}"
    ).json()["integridade"] == "verificada"


def test_sucesso_e_idempotencia(pronta):
    ProvedorControlado.chamadas = 0
    pronta[1].state.envios_telegram.provedor_factory = ProvedorControlado
    response, body = confirmar(pronta)
    assert response.status_code == 201, response.text
    assert response.json()["estado"] == "confirmada"
    replay = pronta[0].post(
        pronta[3] + f"/preparacoes/{pronta[4]}/envio-telegram", json=body
    )
    assert replay.json() == response.json()
    assert ProvedorControlado.chamadas == 1
    with pronta[1].state.contatos.repo.transacao() as session:
        assert session.scalar(select(func.count()).select_from(AutorizacaoEnvioTelegram)) == 1
        tentativa = session.scalars(select(TentativaEnvioTelegram)).one()
        assert (tentativa.message_id, tentativa.conta_envio_id, tentativa.chat_externo_id) == (
            321,
            pronta[2][4][0],
            "123456789",
        )


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        (TimeoutError(), ("desconhecida", "sem_confirmacao", None)),
        (
            (429, json.dumps({"parameters": {"retry_after": 17}}).encode()),
            ("falhou", "limite_telegram", 17),
        ),
    ],
)
def test_timeout_desconhecido_e_http_429(resposta, esperado, monkeypatch):
    provider = Telegram()

    def consultar(*_args):
        if isinstance(resposta, Exception):
            raise NegocioError("telegram_timeout", "Falha controlada.", 504)
        return resposta

    monkeypatch.setattr(provider, "_consultar", consultar)
    result = provider.enviar("123456:token_teste", "123", "texto")
    assert (result.estado, result.codigo, result.retry_after) == esperado


def test_revogacao_concorrente_impede_tentativa(pronta):
    ProvedorControlado.chamadas = 0
    pronta[1].state.envios_telegram.provedor_factory = ProvedorControlado
    c, app, original, root, prep = pronta
    review = c.get(root + f"/preparacoes/{prep}/revisao-envio-telegram").json()
    body = {
        "operacao_id": str(uuid4()),
        "versao": review["versao"],
        "impressao": review["impressao"],
        "marcador_envio": review["marcador_envio"],
        "confirmo_envio_real": True,
    }
    repo = app.state.contatos.repo
    with ThreadPoolExecutor(1) as pool:
        with repo.lock:
            future = pool.submit(
                c.post, root + f"/preparacoes/{prep}/envio-telegram", json=body
            )
            app.state.consentimentos.revogar_telegram(
                original[4][0],
                original[5],
                TelegramEventoEntrada(**telegram_tests.entrada(original).model_dump()),
                original[1],
            )
        assert future.result().status_code == 409
    assert ProvedorControlado.chamadas == 0


def test_falha_persistencia_fecha_como_desconhecida(pronta, monkeypatch):
    ProvedorControlado.chamadas = 0
    pronta[1].state.envios_telegram.provedor_factory = ProvedorControlado
    service = pronta[1].state.envios_telegram
    original = service._persistir_resultado
    calls = 0

    def falhar_uma_vez(tentativa_id, resultado):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NegocioError("persistencia_indisponivel", "Falha controlada.", 503)
        return original(tentativa_id, resultado)

    monkeypatch.setattr(service, "_persistir_resultado", falhar_uma_vez)
    response, _ = confirmar(pronta)
    assert response.status_code == 201, response.text
    assert response.json()["estado"] == "desconhecida"
    assert ProvedorControlado.chamadas == 1


def test_migracao_0015_preserva_dados_existentes(pronta):
    app = pronta[1]
    db = app.state.persistence.database
    with db.engine.connect() as connection:
        before = {
            table: connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()
            for table in (
                "campanhas",
                "campanha_preparacoes",
                "campanha_preparacao_itens_telegram",
                "identidades_telegram",
                "inscricoes_conta",
                "eventos_telegram",
            )
        }
    with bundled_migrations() as location, db.engine.begin() as connection:
        config = migration_config(location)
        config.attributes["connection"] = connection
        command.downgrade(config, "0014_telegram_campanhas")
        command.upgrade(config, "0015_telegram_envio_unitario")
        after = {
            table: connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()
            for table in before
        }
        assert after == before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert read_revision(app.state.persistence.settings.database_path) == (
        "0015_telegram_envio_unitario"
    )


def test_lote_revogacao_concorrente_impede_envio_posterior(lote_rascunho, monkeypatch):
    app, original = lote_rascunho["app"], lote_rascunho["telegram"]
    _, recibo = _iniciar_execucao_lote(lote_rascunho)
    iniciou, liberar, chamadas = Event(), Event(), []

    class ProvedorLote:
        def enviar(self, _token, chat_id, _texto):
            chamadas.append(chat_id)
            iniciou.set()
            assert liberar.wait(10)
            return ResultadoEnvioTelegram("confirmada", "aceita", message_id=321)

        def cancelar(self):
            liberar.set()

    app.state.envios_telegram.provedor_factory = ProvedorLote
    monkeypatch.setattr(app.state.telegram_recepcao, "sincronizar", lambda *_: None)
    contexto, executor, admissao = _executor_lote(lote_rascunho, recibo)
    monkeypatch.setattr(executor, "_esperar_cadencia", lambda *_: None)
    with app.state.contatos.repo.transacao() as session:
        itens = session.scalars(
            select(ItemExecucao)
            .where(ItemExecucao.execucao_id == recibo["execucao_id"])
            .order_by(ItemExecucao.ordem)
        ).all()
        identidade_revogada = itens[1].identidade_telegram_id
    try:
        with ThreadPoolExecutor(2) as pool:
            primeira = pool.submit(executor.passo, admissao)
            assert iniciou.wait(10)
            revogacao = pool.submit(
                app.state.consentimentos.revogar_telegram,
                original[4][0],
                identidade_revogada,
                telegram_tests.entrada(original),
                original[1],
            )
            liberar.set()
            assert primeira.result(timeout=20)["estado"] == "confirmada"
            revogacao.result(timeout=20)
        assert executor.passo(admissao)["processado"] is False
        assert len(chamadas) == 1
        with app.state.contatos.repo.transacao() as session:
            revogado = session.scalar(
                select(ItemExecucao).where(
                    ItemExecucao.execucao_id == recibo["execucao_id"],
                    ItemExecucao.identidade_telegram_id == identidade_revogada,
                )
            )
            assert (revogado.estado, revogado.motivo_codigo) == ("ignorado", "consentimento")
    finally:
        liberar.set()
        contexto.fechar()


def test_lote_resultado_desconhecido_nao_reenvia_apos_reinicio(
    lote_rascunho, monkeypatch
):
    app = lote_rascunho["app"]
    _, recibo = _iniciar_execucao_lote(lote_rascunho)
    chamadas = []

    class ProvedorIncerto:
        def enviar(self, _token, chat_id, _texto):
            chamadas.append(chat_id)
            if len(chamadas) == 1:
                return ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
            return ResultadoEnvioTelegram("confirmada", "aceita", message_id=322)

        def cancelar(self):
            pass

    app.state.envios_telegram.provedor_factory = ProvedorIncerto
    monkeypatch.setattr(app.state.telegram_recepcao, "sincronizar", lambda *_: None)
    contexto, executor, admissao = _executor_lote(lote_rascunho, recibo)
    monkeypatch.setattr(executor, "_esperar_cadencia", lambda *_: None)
    assert executor.passo(admissao)["estado"] == "desconhecida"
    contexto.fechar()

    for _ in range(2):
        recuperacao = ContextoOperacionalMock(
            app.state.execucoes,
            app.state.persistence,
            app.state.execucoes.p.abertura,
            lifetime=SimulatedLifetime(),
        )
        try:
            assert ExecutorTelegram(
                app.state.execucoes,
                app.state.envios_telegram,
                app.state.telegram_recepcao,
                recuperacao,
            ).recuperar()["processamento_reativado"] is False
        finally:
            recuperacao.fechar()
        assert len(chamadas) == 1

    contexto, executor, admissao = _executor_lote(lote_rascunho, recibo, retomar=True)
    monkeypatch.setattr(executor, "_esperar_cadencia", lambda *_: None)
    try:
        assert executor.passo(admissao)["estado"] == "confirmada"
        assert len(chamadas) == len(set(chamadas)) == 2
        with app.state.contatos.repo.transacao() as session:
            tentativas = session.scalars(
                select(TentativaEnvio)
                .where(TentativaEnvio.execucao_id == recibo["execucao_id"])
                .order_by(TentativaEnvio.reservada_em)
            ).all()
            assert [tentativa.estado for tentativa in tentativas] == [
                "desconhecida",
                "aceita_provedor",
            ]
    finally:
        contexto.fechar()


def test_lote_recuperacao_reinicia_pausada_e_exige_nova_confirmacao(
    lote_rascunho,
):
    app, cliente = lote_rascunho["app"], lote_rascunho["cliente"]
    _, recibo = _iniciar_execucao_lote(lote_rascunho)
    execucao_id = recibo["execucao_id"]
    with app.state.contatos.repo.transacao() as session:
        fila_antes = session.scalars(
            select(ItemExecucao.id)
            .where(ItemExecucao.execucao_id == execucao_id)
            .order_by(ItemExecucao.ordem)
        ).all()
        autorizacao_id = session.get(ExecucaoCampanha, execucao_id).autorizacao_telegram_id

    for _ in range(2):
        contexto = ContextoOperacionalMock(
            app.state.execucoes,
            app.state.persistence,
            app.state.execucoes.p.abertura,
            lifetime=SimulatedLifetime(),
        )
        try:
            resultado = ExecutorTelegram(
                app.state.execucoes,
                app.state.envios_telegram,
                app.state.telegram_recepcao,
                contexto,
            ).recuperar()
            assert resultado["processamento_reativado"] is False
        finally:
            contexto.fechar()

    with app.state.contatos.repo.transacao() as session:
        execucao = session.get(ExecucaoCampanha, execucao_id)
        fila_depois = session.scalars(
            select(ItemExecucao.id)
            .where(ItemExecucao.execucao_id == execucao_id)
            .order_by(ItemExecucao.ordem)
        ).all()
        tentativas = session.scalar(
            select(func.count())
            .select_from(TentativaEnvio)
            .where(TentativaEnvio.execucao_id == execucao_id)
        )
        assert (execucao.estado, execucao.autorizacao_telegram_id) == (
            "pausada",
            autorizacao_id,
        )
        assert fila_depois == fila_antes and tentativas == 0

    runtime = app.state.execution_runtime
    lifetime = SimulatedLifetime()
    runtime.lifetime = lifetime
    lifetime.subscribe(runtime.close_admission)
    url = lote_rascunho["root"] + "/execucoes/" + execucao_id
    try:
        revisao = cliente.get(url + "/revisao-retomada")
        assert revisao.status_code == 200, revisao.text
        dados = revisao.json()
        corpo = {
            chave: dados[chave]
            for chave in (
                "versao",
                "modo",
                "preparacao_id",
                "autorizacao_id",
                "versao_autorizacao",
                "geracao",
                "impressao",
                "resumo_configuracao",
            )
        } | {
            "operacao_id": str(uuid4()),
            "marcador_retomada": dados["marcador_retomada"],
            "confirmo_retomada": False,
        }
        assert cliente.post(url + "/retomada", json=corpo).status_code == 422
        with app.state.contatos.repo.transacao() as session:
            assert session.get(ExecucaoCampanha, execucao_id).estado == "pausada"
            assert session.scalar(
                select(func.count())
                .select_from(TentativaEnvio)
                .where(TentativaEnvio.execucao_id == execucao_id)
            ) == 0
    finally:
        lifetime.close()
