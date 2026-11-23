"""Contas: integridade, autorização, concorrência e idempotência."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_contatos
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.auth.models import Usuario
from app.contas_envio.models import ContaEnvio
from app.contas_envio.schemas import ContaEdicao, ContaEntrada
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria
from app.provedores.telegram import Telegram

cadastro = test_contatos.cadastro


def entrada(**changes):
    return {
        "operacao_id": str(uuid4()),
        "nome": "Conta local",
        "plataforma": "telegram",
        "provedor": "telegram_bot_api",
        **changes,
    }


def criar(c, **changes):
    response = c.post("/contas-envio", json=entrada(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def telegram_cadastrado(c, secret="123456:sentinela_telegram_A"):
    conta = criar(c)
    root = "/contas-envio/" + conta["id"]
    response = c.put(
        root + "/credencial",
        json={"operacao_id": str(uuid4()), "versao": 1, "credencial": secret},
    )
    assert response.status_code == 200
    return root


def test_telegram_sucesso_isolamento_sem_persistir(cadastro, monkeypatch, caplog):
    from app.provedores import telegram

    c, app, _ = cadastro
    secrets = ["123456:sentinela_telegram_A", "987654:sentinela_telegram_B"]
    roots = [telegram_cadastrado(c, secret) for secret in secrets]
    chamadas, fechadas = [], []

    class Connection:
        def __init__(self, host, timeout):
            assert (host, timeout) == ("api.telegram.org", 8)

        def request(self, method, path):
            assert method == "GET"
            chamadas.append(path)

        def getresponse(self):
            return self

        status = 200

        def read(self, limit):
            assert limit == 65537
            return json.dumps(
                {
                    "ok": True,
                    "result": {
                        "id": 123456,
                        "is_bot": True,
                        "first_name": "Bot local",
                        "username": "local_bot",
                        "ignorado": secrets[0],
                    },
                }
            ).encode()

        def close(self):
            fechadas.append(True)

    monkeypatch.setattr(telegram, "HTTPSConnection", Connection)
    before = [c.get(root).json() for root in roots]
    with app.state.contatos.repo.transacao() as s:
        auditoria = list(s.scalars(select(Auditoria.id)))
    for root in roots:
        response = c.post(root + "/verificar-telegram")
        assert response.status_code == 200
        assert response.json() == {"id": 123456, "nome": "Bot local", "username": "local_bot"}
        assert all(secret not in response.text for secret in secrets)
    assert chamadas == [f"/bot{secret}/getMe" for secret in secrets]
    assert len(fechadas) == 2
    assert [c.get(root).json() for root in roots] == before
    with app.state.contatos.repo.transacao() as s:
        assert list(s.scalars(select(Auditoria.id))) == auditoria
    assert all(secret not in caplog.text for secret in secrets)


@pytest.mark.parametrize(
    "status,body,error,expected,code",
    [
        (401, b"sentinela", None, 422, "telegram_credencial_invalida"),
        (404, b"sentinela", None, 422, "telegram_credencial_invalida"),
        (500, b"sentinela", None, 502, "telegram_http"),
        (302, b"sentinela", None, 502, "telegram_http"),
        (200, b"sentinela", TimeoutError, 504, "telegram_timeout"),
        (200, b"sentinela", OSError, 502, "telegram_comunicacao"),
        (200, b"not json", None, 502, "telegram_resposta"),
        (200, b'{"ok":true,"result":[]}', None, 502, "telegram_resposta"),
        (
            200,
            b'{"ok":true,"result":{"id":true,"is_bot":true,"first_name":"Bot"}}',
            None,
            502,
            "telegram_resposta",
        ),
    ],
)
def test_telegram_falhas_sanitizadas(cadastro, caplog, status, body, error, expected, code):
    c, app, _ = cadastro
    secret = "123456:sentinela_telegram_A"
    root = telegram_cadastrado(c, secret)

    def transporte(token):
        assert token == secret
        if error:
            raise error(f"https://api.telegram.org/bot{token}/getMe")
        return status, body

    app.state.contas_envio.telegram = Telegram(transporte)
    response = c.post(root + "/verificar-telegram")
    assert response.status_code == expected
    assert response.json()["codigo"] == code
    with pytest.raises(NegocioError) as exc:
        app.state.contas_envio.telegram.verificar(secret)
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)
    assert secret not in str(exc.value.args)
    assert exc.value.__context__ is None
    assert secret not in response.text + caplog.text


def test_telegram_autorizacao_e_precondicoes(cadastro):
    c, app, token = cadastro
    root = "/contas-envio/" + criar(c)["id"]

    def proibido(_):
        pytest.fail("Não deve chamar Telegram")

    app.state.contas_envio.telegram = Telegram(proibido)
    assert c.post(root + "/verificar-telegram").json()["codigo"] == "telegram_sem_credencial"
    assert c.post(root + "/verificar-telegram", headers={"X-eDisk-Session": ""}).status_code == 401
    assert c.post(root + "/verificar-telegram", headers={"Authorization": ""}).status_code == 401
    assert c.post("/contas-envio/" + str(uuid4()) + "/verificar-telegram").status_code == 404
    c.patch(root, json={"operacao_id": str(uuid4()), "versao": 1, "ativo": False})
    assert c.post(root + "/verificar-telegram").json()["codigo"] == "telegram_conta_invalida"
    with app.state.persistence.database.transaction() as s:
        s.execute(update(Usuario).values(perfil="operador"))
    assert c.post(root + "/verificar-telegram").status_code == 403
    with pytest.raises(NegocioError) as exc:
        app.state.contas_envio.verificar_telegram(root.split("/")[-1], token)
    assert exc.value.status == 403


def test_telegram_descarta_resultado_se_conta_muda(cadastro):
    c, app, _ = cadastro
    root = telegram_cadastrado(c)

    def transporte(_):
        assert (
            c.patch(
                root,
                json={
                    "operacao_id": str(uuid4()),
                    "versao": 2,
                    "ativo": False,
                },
            ).status_code
            == 200
        )
        return 200, b'{"ok":true,"result":{"id":123,"is_bot":true,"first_name":"Bot"}}'

    app.state.contas_envio.telegram = Telegram(transporte)
    assert c.post(root + "/verificar-telegram").status_code == 409


def test_ciclo_de_vida_replay_e_preservacao(cadastro):
    c, app, _ = cadastro
    body = entrada()
    first = c.post("/contas-envio", json=body).json()
    root = "/contas-envio/" + first["id"]
    assert c.post("/contas-envio", json=body).json() == first
    assert c.post("/contas-envio", json={**body, "nome": "Outra"}).status_code == 409
    criar(c)
    assert c.get("/contas-envio", params={"q": "LOCAL", "limite": 1}).json()["total"] == 2
    assert c.get("/contas-envio", params={"q": "%"}).json()["total"] == 0
    assert c.get("/contas-envio", params={"provedor": "meta_cloud"}).json()["total"] == 0
    edit = {"operacao_id": str(uuid4()), "versao": 1, "nome": "Renomeada", "ativo": False}
    assert c.patch(root, json=edit).json()["versao"] == 2
    assert c.patch(root, json=edit).json()["versao"] == 2
    assert c.get(root).json()["estado_cadastral"] == "desativada"
    assert c.get("/contas-envio", params={"estado": "desativada"}).json()["total"] == 1
    assert c.patch(root, json={**edit, "operacao_id": str(uuid4())}).status_code == 409
    assert (
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 2, "ativo": True}).status_code
        == 200
    )
    remove = {"operacao_id": str(uuid4()), "versao": 3, "remover": True}
    assert c.patch(root, json=remove).status_code == 200
    assert c.get(root).json()["estado_cadastral"] == "removida"
    assert (
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 4, "ativo": True}).status_code
        == 409
    )
    assert c.get("/contas-envio", params={"estado": "removida"}).json()["total"] == 1
    assert c.patch(root, json=edit).json()["versao"] == 2
    assert c.get(root).json()["conexao"] == "nao_verificada"
    with app.state.contatos.repo.transacao() as s:
        assert "Renomeada" not in " ".join(s.scalars(select(Auditoria.resumo_json)))
        assert len(s.scalars(select(ContaEnvio)).all()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"provedor": "meta_cloud"},
        {"plataforma": "mock"},
        {"nome": "  "},
        {"nome": "A" * 121},
        {"nome": "a\u0000b"},
        {"configuracao_publica_json": {"token": "x"}},
        {"segredo_ref": "../externo"},
        {"ativo": True},
    ],
)
def test_entradas_estritas(cadastro, changes):
    assert cadastro[0].post("/contas-envio", json=entrada(**changes)).status_code == 422


def test_constraints_e_identidade_imutavel(cadastro):
    c, app, _ = cadastro
    first = criar(c)
    for sql in [
        "UPDATE contas_envio SET plataforma='whatsapp'",
        "UPDATE contas_envio SET nome=''",
        "UPDATE contas_envio SET versao=0",
        "UPDATE contas_envio SET segredo_ref='../fora'",
        "UPDATE contas_envio SET removido_em=criado_em",
        "INSERT OR REPLACE INTO contas_envio SELECT * FROM contas_envio",
        "DELETE FROM contas_envio",
    ]:
        with pytest.raises(IntegrityError), app.state.persistence.database.transaction() as s:
            s.execute(text(sql))
    assert (
        c.patch(
            "/contas-envio/" + first["id"],
            json={"operacao_id": str(uuid4()), "versao": 1, "provedor": "meta_cloud"},
        ).status_code
        == 422
    )


def test_operador_bloqueado_no_servico_e_api(cadastro):
    c, app, token = cadastro
    first = criar(c)
    with app.state.persistence.database.transaction() as s:
        s.execute(update(Usuario).values(perfil="operador"))
    root = "/contas-envio/" + first["id"]
    for response in [
        c.get("/contas-envio"),
        c.get(root),
        c.post("/contas-envio", json=entrada()),
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 1, "ativo": False}),
    ]:
        assert response.status_code == 403
    with pytest.raises(NegocioError) as error:
        app.state.contas_envio.criar(ContaEntrada(**entrada()), token)
    assert error.value.status == 403


def test_concorrencia_replay_e_revisao(cadastro):
    _, app, token = cadastro
    svc = app.state.contas_envio
    data = ContaEntrada(**entrada())
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: svc.criar(data, token), range(4)))
    assert all(r == results[0] for r in results)
    identifier = results[0]["id"]

    def editar(i):
        try:
            return svc.editar(
                identifier, ContaEdicao(operacao_id=uuid4(), versao=1, nome=f"Conta {i}"), token
            )
        except NegocioError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(editar, range(4)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count(409) == 3
