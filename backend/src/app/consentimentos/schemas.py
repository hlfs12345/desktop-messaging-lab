"""Contratos estritos; operador e instante de registro vêm do servidor."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.contatos.schemas import Entrada

Categoria = Literal["promocoes", "atualizacoes_solicitadas", "eventos_grupos"]
Estado = Literal["desconhecido", "autorizado", "bloqueado"]


class OperacaoEntrada(Entrada):
    @field_validator("*", mode="before")
    @classmethod
    def uuid_explicito(cls, value, info):
        if (
            info.field_name.endswith("_id")
            and not info.field_name.endswith("_externo_id")
            and isinstance(value, str)
        ):
            try:
                return UUID(value)
            except ValueError:
                raise ValueError("UUID invalido.") from None
        return value


class EscopoEntrada(OperacaoEntrada):
    operacao_id: UUID
    nome: str = Field(min_length=1, max_length=120)

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, value):
        if not value.strip():
            raise ValueError("Informe o nome.")
        return value.strip()


class IdentidadeEntrada(OperacaoEntrada):
    operacao_id: UUID
    telefone_id: UUID
    canal: Literal["whatsapp"]
    versao: int = Field(ge=0)


class EventoEntrada(OperacaoEntrada):
    operacao_id: UUID
    escopo_id: UUID
    versao: int = Field(ge=0)
    tipo: Literal[
        "autorizou",
        "revogou",
        "bloqueio_global",
        "desbloqueio_global",
        "bloqueio_canal",
        "desbloqueio_canal",
    ]
    identidade_contato_id: UUID | None = None
    categoria: Categoria | None = None
    origem: Literal["operador"]
    ocorrido_em: datetime
    metodo_captura: str | None = Field(default=None, min_length=1, max_length=120)
    evidencia_referencia: str | None = Field(default=None, min_length=1, max_length=500)
    justificativa: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("ocorrido_em", mode="before")
    @classmethod
    def data_com_fuso(cls, value):
        # JSON estrito não permite coerção implícita em model_validate; parser explícito.
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("Informe uma data válida.") from None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Informe o fuso horário.")
        return value

    @field_validator("metodo_captura", "evidencia_referencia", "justificativa")
    @classmethod
    def texto_preenchido(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Informe o valor.")
        return value.strip() if value else value

    @model_validator(mode="after")
    def alvo(self):
        global_ = self.tipo.endswith("_global")
        categoria = self.tipo in {"autorizou", "revogou"}
        if global_ != (self.identidade_contato_id is None):
            raise ValueError("Alvo inválido.")
        if categoria != (self.categoria is not None):
            raise ValueError("Categoria inválida.")
        if self.tipo == "autorizou":
            if not self.metodo_captura or not self.evidencia_referencia:
                raise ValueError("Autorização exige evidência e método.")
        elif not self.justificativa:
            raise ValueError("Informe a justificativa.")
        return self


class ListaAutorizacaoEntrada(OperacaoEntrada):
    operacao_id: UUID
    escopo_id: UUID
    categoria: Categoria
    ocorrido_em: datetime
    metodo_captura: str = Field(min_length=1, max_length=120)
    evidencia_referencia: str = Field(min_length=1, max_length=500)

    @field_validator("ocorrido_em", mode="before")
    @classmethod
    def data_com_fuso(cls, value):
        return EventoEntrada.data_com_fuso(value)

    @field_validator("metodo_captura", "evidencia_referencia")
    @classmethod
    def texto_preenchido(cls, value):
        if not value.strip():
            raise ValueError("Informe o valor.")
        return value.strip()


class IdentidadeTelegramEntrada(OperacaoEntrada):
    operacao_id: UUID
    usuario_externo_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    chat_externo_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")

    @model_validator(mode="after")
    def chat_privado(self):
        if self.usuario_externo_id != self.chat_externo_id:
            raise ValueError("Somente interação privada do próprio usuário.")
        return self


class TelegramEventoEntrada(OperacaoEntrada):
    operacao_id: UUID
    escopo_id: UUID
    bot_externo_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")
    evento_externo_id: str = Field(min_length=1, max_length=100)
    ocorrido_em: datetime

    @field_validator("ocorrido_em", mode="before")
    @classmethod
    def data_com_fuso(cls, value):
        return EventoEntrada.data_com_fuso(value)

    @field_validator("evento_externo_id")
    @classmethod
    def evento_preenchido(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("Informe referência externa sem espaços nas extremidades.")
        return value


class TelegramAceiteEntrada(TelegramEventoEntrada):
    categoria: Categoria
    aviso_versao: str = Field(min_length=1, max_length=80)
    aviso_texto: str = Field(min_length=1, max_length=2000)
    confirmado: bool = Field(strict=True)

    @model_validator(mode="after")
    def aceite_explicito(self):
        if not self.confirmado or not self.aviso_versao.strip() or not self.aviso_texto.strip():
            raise ValueError("Confirme explicitamente a finalidade e o aviso apresentado.")
        return self
