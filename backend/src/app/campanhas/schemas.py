"""Entradas limitadas e explícitas; nenhum estado executável."""

from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.campanhas.configuracao_simulacao import ConfiguracaoSimulacao
from app.consentimentos.schemas import Categoria, OperacaoEntrada
from app.contas_envio.schemas import ContaEntrada, RevisaoEntrada
from app.contatos.schemas import Entrada


class RascunhoEntrada(OperacaoEntrada):
    operacao_id: UUID
    nome: str = Field(min_length=1, max_length=120)
    conta_envio_id: UUID
    escopo_id: UUID
    template_versao_id: UUID
    categoria: Categoria
    listas: list[UUID] = Field(default_factory=list, max_length=200)

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, value):
        return ContaEntrada.nome_valido(value)

    @field_validator("listas", mode="before")
    @classmethod
    def listas_uuid(cls, value):
        if isinstance(value, list):
            return [UUID(i) if isinstance(i, str) else i for i in value]
        return value

    @field_validator("listas")
    @classmethod
    def listas_unicas(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Listas repetidas.")
        return value


class RascunhoEdicao(RevisaoEntrada):
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    conta_envio_id: UUID | None = None
    escopo_id: UUID | None = None
    template_versao_id: UUID | None = None
    categoria: Categoria | None = None
    listas: list[UUID] | None = Field(default=None, max_length=200)

    @field_validator("listas", mode="before")
    @classmethod
    def listas_uuid(cls, value):
        return RascunhoEntrada.listas_uuid(value)

    @model_validator(mode="after")
    def alteracao(self):
        fields = self.model_fields_set - {"operacao_id", "versao"}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError("Informe alterações válidas.")
        if self.nome is not None:
            self.nome = ContaEntrada.nome_valido(self.nome)
        if self.listas is not None:
            RascunhoEntrada.listas_unicas(self.listas)
        return self


class SelecaoEntrada(RevisaoEntrada):
    identidade_contato_id: UUID | None = None
    identidade_telegram_id: UUID | None = None
    selecionada: bool

    @model_validator(mode="after")
    def identidade_unica(self):
        if (self.identidade_contato_id is None) == (self.identidade_telegram_id is None):
            raise ValueError("Informe exatamente uma identidade de canal.")
        return self


class SelecaoElegiveisEntrada(RevisaoEntrada):
    pass


class PreviaEntrada(Entrada):
    versao: int = Field(ge=1)
    nome: str | None = Field(default=None, min_length=1, max_length=120, repr=False)

    @field_validator("nome")
    @classmethod
    def nome_preenchido(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Informe um nome fictício de até 120 caracteres.")
        return value


class PrepararEntrada(RevisaoEntrada):
    marcador: str = Field(min_length=1, max_length=180)
    configuracao_simulacao: ConfiguracaoSimulacao | None = None

    @field_validator("configuracao_simulacao", mode="before")
    @classmethod
    def configuracao_explicita(cls, value):
        if value is None:
            raise ValueError("Configuracao nao pode ser nula.")
        return value


class TransicaoEntrada(RevisaoEntrada):
    preparacao_id: UUID
    impressao: str | None = Field(default=None, min_length=64, max_length=64)


class ChaveEntrada(OperacaoEntrada):
    operacao_id: UUID


class CodigoEntrada(ChaveEntrada):
    codigo: SecretStr = Field(min_length=1, max_length=160, repr=False)


class AutorizarSimulacaoEntrada(RevisaoEntrada):
    impressao: str = Field(pattern=r"^[0-9a-f]{64}$")
    resumo_configuracao: str = Field(pattern=r"^[0-9a-f]{64}$")
    marcador_autorizacao: str = Field(pattern=r"^autorizacao-v1\.[0-9a-f]{64}$")
    confirmo_simulacao: bool

    @field_validator("confirmo_simulacao")
    @classmethod
    def confirmacao_explicita(cls, value):
        if value is not True:
            raise ValueError("Confirme explicitamente a simulacao.")
        return value


class RevogarSimulacaoEntrada(RevisaoEntrada):
    motivo_codigo: Literal["revogacao_manual"]


class ConfirmarEnvioTelegramEntrada(RevisaoEntrada):
    impressao: str = Field(pattern=r"^[0-9a-f]{64}$")
    marcador_envio: str = Field(pattern=r"^envio-telegram-v1\.[0-9a-f]{64}$")
    confirmo_envio_real: bool

    @field_validator("confirmo_envio_real")
    @classmethod
    def confirmacao_explicita(cls, value):
        if value is not True:
            raise ValueError("Confirme explicitamente o envio real.")
        return value


class ConfirmarEnvioWhatsappEntrada(RevisaoEntrada):
    impressao: str = Field(pattern=r"^[0-9a-f]{64}$")
    marcador_envio: str = Field(pattern=r"^envio-whatsapp-v1\.[0-9a-f]{64}$")
    confirmo_envio_real: bool

    @field_validator("confirmo_envio_real")
    @classmethod
    def confirmacao_explicita(cls, value):
        if value is not True:
            raise ValueError("Confirme explicitamente o envio real.")
        return value
