"""Contrato estrito e representacao canonica sem dados de destinatarios."""

import hashlib
from copy import deepcopy
from typing import get_args

import pytest
from pydantic import ValidationError

from app.campanhas.configuracao_simulacao import (
    CenarioSimulacao,
    ConfiguracaoSimulacao,
    canon_configuracao,
    criar_configuracao,
    resumo_configuracao,
    validar_cabecalho,
)
from app.provedores.mock import VERSAO_CONTRATO_MOCK, CenarioMock

ACCOUNT = "00000000-0000-0000-0000-000000000016"
MARKER = ACCOUNT + "." + ACCOUNT + ".2.30"


def configuration():
    return criar_configuracao(ACCOUNT, MARKER, "aceita_provedor", ACCOUNT)


def test_cenarios_contrato_canonicalizacao_e_imutabilidade():
    assert set(get_args(CenarioSimulacao)) == {s.value for s in CenarioMock}
    assert configuration().versao_contrato_mock == VERSAO_CONTRATO_MOCK
    value = {"b": False, "a": "a\u00e7\u00e3o"}
    expected = b'{"a":"a\\u00e7\\u00e3o","b":false}'
    assert canon_configuracao(value) == expected
    assert canon_configuracao(dict(reversed(list(value.items())))) == expected
    assert resumo_configuracao(value) == hashlib.sha256(expected).hexdigest()
    cfg = configuration()
    assert cfg == configuration()
    assert (
        cfg.configuracao_id
        != criar_configuracao(ACCOUNT, MARKER, "aceita_provedor", "outra-campanha").configuracao_id
    )
    assert (
        cfg.resumo_configuracao
        != criar_configuracao(ACCOUNT, MARKER, "falhou", ACCOUNT).resumo_configuracao
    )
    with pytest.raises(ValidationError):
        cfg.cenario = "falhou"
    with pytest.raises(ValidationError):
        cfg.capacidades.texto = False
    payload = cfg.model_dump()
    clone = ConfiguracaoSimulacao.model_validate(payload)
    payload["capacidades"]["texto"] = False
    assert clone.capacidades.texto is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("modo", "real"),
        ("provedor", "meta_cloud"),
        ("plataforma", "telegram"),
        ("configuracao_id", "invalido"),
        ("conta_envio_id", "0" * 36),
        ("versao_configuracao", True),
        ("versao_configuracao", "1"),
        ("versao_configuracao", 2),
        ("versao_contrato_mock", "futuro"),
        ("politica_cenarios", "aleatoria"),
        ("cenario", ""),
        ("cenario", "sucesso"),
        ("cenario", None),
        ("resumo_configuracao", "a" * 64),
        ("texto", "sentinela-nao-admitida"),
        ("destino", "sentinela-nao-admitida"),
    ],
)
def test_campos_invalidos(field, value):
    payload = configuration().model_dump()
    payload[field] = value
    if field != "resumo_configuracao":
        payload["resumo_configuracao"] = resumo_configuracao(
            {k: v for k, v in payload.items() if k != "resumo_configuracao"}
        )
    with pytest.raises(ValidationError):
        ConfiguracaoSimulacao.model_validate(payload)


@pytest.mark.parametrize("field", list(configuration().model_dump()))
def test_nenhum_campo_pode_ser_omitido(field):
    payload = configuration().model_dump()
    del payload[field]
    with pytest.raises(ValidationError):
        ConfiguracaoSimulacao.model_validate(payload)


@pytest.mark.parametrize("value", [1, "true", False, None])
def test_capacidades_estritas(value):
    payload = configuration().model_dump()
    payload["capacidades"]["texto"] = value
    payload["resumo_configuracao"] = resumo_configuracao(
        {k: v for k, v in payload.items() if k != "resumo_configuracao"}
    )
    with pytest.raises(ValidationError):
        ConfiguracaoSimulacao.model_validate(payload)


def test_cabecalho_vincula_conta_plataforma_e_marcador():
    cfg = configuration().model_dump()
    header = {
        "provedor": "mock",
        "plataforma": "whatsapp",
        "conta_envio_id": ACCOUNT,
        "campanha_id": ACCOUNT,
        "marcador": MARKER + "." + cfg["resumo_configuracao"],
        "configuracao_simulacao": cfg,
    }
    validar_cabecalho(header)
    for field, value in (
        ("conta_envio_id", "outro"),
        ("campanha_id", "outra"),
        ("plataforma", "telegram"),
        ("marcador", MARKER),
        ("provedor", "meta_cloud"),
    ):
        changed = deepcopy(header)
        changed[field] = value
        with pytest.raises(ValueError):
            validar_cabecalho(changed)
