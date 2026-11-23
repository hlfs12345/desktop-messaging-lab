"""Cadastro/HTTP reais em SQLite temporário, sem dados de produção."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from app.auth.models import Sessao
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria, Contato, ListaContato
from app.contatos.normalizacao import telefone
from app.main import create_app


@pytest.fixture
def cadastro(settings, authorization):
    app = create_app(settings, authorization=authorization)
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {authorization.token}"}
    ) as client:
        data = {
            "nome": "Pessoa Fictícia",
            "login": "teste",
            "senha": "frase fictícia de teste 489!",
        }
        assert client.post("/auth/configuracao-inicial", json=data).status_code == 201
        token = client.post("/auth/login", json={k: data[k] for k in ("login", "senha")}).json()[
            "token"
        ]
        client.headers["X-eDisk-Session"] = token
        yield client, app, token


def criar(client, **kwargs):
    return client.post("/contatos", json={"telefone": "+5511912345678", "nome": "José", **kwargs})


@pytest.mark.parametrize(
    "value,region,expected",
    [
        ("(11) 91234-5678", "BR", "+5511912345678"),
        ("+44 20 8366 1177", None, "+442083661177"),
        ("020 8366 1177", "GB", "+442083661177"),
        ("+39 02 3661 8300", None, "+390236618300"),
        ("+1 (650) 253-0000", None, "+16502530000"),
    ],
)
def test_internacionais(value, region, expected):
    assert telefone(value, region) == expected


@pytest.mark.parametrize(
    "value,region",
    [
        ("11912345678", None),
        ("912345678", "BR"),
        ("11 1234 5678", "BR"),
        ("123", "BR"),
        ("+55 11 91234-5678 ramal 2", None),
        ("+5511912345678/+442083661177", None),
        ("+5511912345678", "ZZ"),
    ],
)
def test_invalidos_sem_inferencia(value, region):
    with pytest.raises(NegocioError):
        telefone(value, region)


def test_crud_multiplos_telefones_versao_e_arquivamento(cadastro):
    c, app, _ = cadastro
    item = criar(c).json()
    assert criar(c).status_code == 409
    assert (
        c.patch("/contatos/" + item["id"], json={"versao": 1, "nome": "Maria"}).status_code == 200
    )
    assert (
        c.patch("/contatos/" + item["id"], json={"versao": 1, "nome": "Antigo"}).status_code == 409
    )
    phone = c.post(
        "/contatos/" + item["id"] + "/telefones", json={"telefone": "+442083661177"}
    ).json()
    assert len(c.get("/contatos/" + item["id"]).json()["telefones"]) == 2
    assert (
        c.patch(
            f"/contatos/{item['id']}/telefones/{phone['id']}", json={"versao": 1, "ativo": False}
        ).status_code
        == 200
    )
    assert criar(c, telefone="+442083661177").status_code == 409
    assert c.patch("/contatos/" + item["id"], json={"versao": 2, "ativo": False}).status_code == 200
    assert c.get("/contatos").json()["total"] == 0
    assert c.get("/contatos", params={"ativo": "false"}).json()["total"] == 1
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Contato)) == 1
        assert "Maria" not in str(s.scalars(select(Auditoria.resumo_json)).all())


def test_sem_alteracao_preserva_data(cadastro):
    c, _, _ = cadastro
    item = criar(c, nome=None).json()
    edited = c.patch("/contatos/" + item["id"], json={"versao": 1, "nome": None}).json()
    assert edited["atualizado_em"] == item["atualizado_em"]
    assert edited["versao"] == 1


def test_listas_associacao_idempotente_busca_paginacao(cadastro):
    c, app, _ = cadastro
    item = criar(c).json()
    group = c.post("/listas", json={"nome": "Clientes"}).json()
    assert c.post("/listas", json={"nome": "CLIENTES"}).status_code == 409
    url = f"/listas/{group['id']}/contatos/{item['id']}"
    assert c.put(url).status_code == 204
    assert c.put(url).status_code == 204
    assert c.get("/contatos", params={"q": "JOSE", "lista_id": group["id"]}).json()["total"] == 1
    assert c.get("/contatos", params={"q": "%"}).json()["total"] == 0
    assert c.get("/contatos", params={"q": "+5511912"}).json()["total"] == 1
    assert c.get("/contatos", params={"limite": 101}).status_code == 422
    assert c.delete(url).status_code == 204
    assert c.get("/contatos").json()["total"] == 1
    assert c.patch("/listas/" + group["id"], json={"versao": 1, "ativo": False}).status_code == 200
    assert c.put(url).status_code == 409
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(ListaContato)) == 0


def test_duas_credenciais_e_upload_antes_parser(cadastro):
    c, app, _ = cadastro
    c.headers.pop("X-eDisk-Session")
    assert criar(c).status_code == 401
    assert (
        c.put(
            "/importacoes/00000000-0000-0000-0000-000000000002/arquivo",
            content=b"x" * (10 * 1024 * 1024 + 1),
        ).status_code
        == 401
    )
    assert c.get("/health").status_code == 200


def test_edicoes_concorrentes(cadastro):
    c, _, _ = cadastro
    item = criar(c).json()

    def editar(name):
        return c.patch("/contatos/" + item["id"], json={"versao": 1, "nome": name}).status_code

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(editar, ["Primeiro", "Segundo"])) == [200, 409]


def test_falha_commit_reverte_e_sanitiza(cadastro):
    c, app, _ = cadastro

    def fail(_):
        raise OperationalError("SQL privado", {}, Exception("PII privada"))

    engine = app.state.persistence.database.engine
    event.listen(engine, "commit", fail)
    try:
        response = criar(c)
        assert response.status_code == 503
        assert "privad" not in response.text
    finally:
        event.remove(engine, "commit", fail)
    assert c.get("/contatos").json()["total"] == 0


def test_expiracao_sessao(cadastro):
    c, app, _ = cadastro
    with app.state.contatos.repo.transacao() as s:
        now = datetime.now(UTC)
        s.execute(
            update(Sessao).values(
                criado_em=now - timedelta(hours=2), expira_em=now - timedelta(hours=1)
            )
        )
    assert c.get("/contatos").status_code == 401


def test_resposta_excessiva_sanitizada(cadastro, monkeypatch):
    c, app, _ = cadastro
    monkeypatch.setattr(
        app.state.contatos, "listar", lambda *args: {"privado": "x" * (512 * 1024 + 1)}
    )
    result = c.get("/contatos")
    assert result.status_code == 413
    assert result.json()["codigo"] == "resposta_excessiva"
    assert result.headers["cache-control"] == "no-store"
    assert "privado" not in result.text
