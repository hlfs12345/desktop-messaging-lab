"""Configuracao de simulacao estrita; sem payload, execucao ou credenciais."""

import hashlib
import hmac
import json
from dataclasses import asdict
from typing import Literal
from uuid import NAMESPACE_OID, UUID, uuid5

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.contatos.schemas import Entrada
from app.provedores.contratos import CapacidadesProvedor
from app.provedores.mock import VERSAO_CONTRATO_MOCK

CenarioSimulacao = Literal["aceita_provedor", "falhou", "desconhecida", "interrupcao", "excecao"]


def canon_configuracao(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
        "ascii"
    )


def resumo_configuracao(value):
    return hashlib.sha256(canon_configuracao(value)).hexdigest()


class CapacidadesSimulacao(Entrada):
    model_config = ConfigDict(frozen=True)
    texto: bool
    simulacao: bool
    entrega: bool
    leitura: bool
    botoes: bool
    anexos: bool
    conexao_externa_verificada: bool

    @model_validator(mode="after")
    def suportadas(self):
        if self.model_dump() != asdict(CapacidadesProvedor(texto=True, simulacao=True)):
            raise ValueError("Capacidades de simulacao incompativeis.")
        return self


class ConfiguracaoSimulacao(Entrada):
    model_config = ConfigDict(frozen=True)
    configuracao_id: str
    modo: Literal["simulacao"]
    provedor: Literal["mock"]
    plataforma: Literal["whatsapp"]
    conta_envio_id: str
    versao_configuracao: int = Field(strict=True, ge=1, le=1)
    versao_contrato_mock: Literal["texto_v1"]
    politica_cenarios: Literal["cenario_unico_v1"]
    cenario: CenarioSimulacao
    capacidades: CapacidadesSimulacao
    resumo_configuracao: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("configuracao_id", "conta_envio_id")
    @classmethod
    def identificador(cls, value):
        parsed = UUID(value)
        if not parsed.int or str(parsed) != value:
            raise ValueError("Identificador de configuracao invalido.")
        return value

    @model_validator(mode="after")
    def resumo_valido(self):
        expected = resumo_configuracao(self.model_dump(exclude={"resumo_configuracao"}))
        if not hmac.compare_digest(self.resumo_configuracao, expected):
            raise ValueError("Resumo de configuracao divergente.")
        return self


def criar_configuracao(conta_id, marcador, cenario, campanha_id):
    value = {
        "configuracao_id": str(
            uuid5(
                NAMESPACE_OID,
                canon_configuracao([campanha_id, marcador, conta_id, cenario]).decode("ascii"),
            )
        ),
        "modo": "simulacao",
        "provedor": "mock",
        "plataforma": "whatsapp",
        "conta_envio_id": conta_id,
        "versao_configuracao": 1,
        "versao_contrato_mock": VERSAO_CONTRATO_MOCK,
        "politica_cenarios": "cenario_unico_v1",
        "cenario": cenario,
        "capacidades": asdict(CapacidadesProvedor(texto=True, simulacao=True)),
    }
    return ConfiguracaoSimulacao.model_validate(
        {
            **value,
            "resumo_configuracao": resumo_configuracao(value),
        }
    )


def validar_cabecalho(header):
    if header["provedor"] != "mock":
        if "configuracao_simulacao" in header:
            raise ValueError("Configuracao simulada em fotografia real.")
        if header["provedor"] == "telegram_bot_api" and (
            header["plataforma"] != "telegram" or header.get("tipo_autorizacao") != "envio_real"
        ):
            raise ValueError("Vinculo Telegram divergente.")
        return
    cfg = ConfiguracaoSimulacao.model_validate(header["configuracao_simulacao"])
    parts = header["marcador"].split(".")
    if (
        header["plataforma"] != cfg.plataforma
        or header["conta_envio_id"] != cfg.conta_envio_id
        or len(parts) != 5
        or parts[4] != cfg.resumo_configuracao
        or cfg
        != criar_configuracao(
            cfg.conta_envio_id, ".".join(parts[:4]), cfg.cenario, header["campanha_id"]
        )
    ):
        raise ValueError("Vinculo de configuracao divergente.")
