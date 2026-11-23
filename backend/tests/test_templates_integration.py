"""Templates preservam consentimentos e a invalidação conservadora de importações."""

from uuid import uuid4

import test_consentimentos
import test_contatos
from sqlalchemy import text
from test_importacoes import confirmar, preparar
from test_templates import criar

cadastro = test_contatos.cadastro
consentimento = test_consentimentos.consentimento


def test_templates_nao_modificam_dados_de_contatos_ou_consentimentos(consentimento):
    c, app, _, _, _, _, _ = consentimento
    test_consentimentos.registrar(consentimento)
    db = app.state.persistence.database
    tables = (
        "contatos",
        "telefones_contato",
        "listas",
        "lista_contatos",
        "escopos_comunicacao",
        "identidades_contato",
        "preferencias_contato",
        "eventos_consentimento",
    )
    with db.transaction() as s:
        before = {t: s.execute(text("SELECT * FROM " + t)).all() for t in tables}
    item = criar(c)
    root = "/templates/" + item["id"]
    assert (
        c.post(
            root + "/versoes",
            json={
                "operacao_id": str(uuid4()),
                "plataforma": "telegram",
                "conteudo": "Texto local",
            },
        ).status_code
        == 201
    )
    assert (
        c.patch(
            root,
            json={
                "operacao_id": str(uuid4()),
                "versao": 2,
                "nome": "Nome atualizado",
                "ativo": False,
            },
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/templates/previa",
            json={
                "plataforma": "telegram",
                "conteudo": "Olá {{nome}}",
                "valores": {"nome": "Pessoa fictícia"},
            },
        ).status_code
        == 200
    )
    assert test_consentimentos.avaliar(consentimento)["elegivel_consentimento"]
    with db.transaction() as s:
        assert {t: s.execute(text("SELECT * FROM " + t)).all() for t in tables} == before


def test_auditoria_de_template_invalida_previa_de_importacao(cadastro):
    c, _, _ = cadastro
    identifier, state = preparar(cadastro, b"nome;telefone\nFicticio;+442083661177\n")
    assert state["status"] == "pronta"
    criar(c)
    result = confirmar(c, identifier)
    assert result.status_code == 409
    assert result.json()["codigo"] == "previa_desatualizada"
    assert c.get("/contatos").json()["total"] == 0
