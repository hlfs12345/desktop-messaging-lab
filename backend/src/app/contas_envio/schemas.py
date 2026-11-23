"""Entradas estritas; credencial nunca participa da representação de diagnóstico."""

import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.consentimentos.schemas import OperacaoEntrada

Plataforma = Literal["whatsapp", "telegram"]
Provedor = Literal["qr_candidato", "meta_cloud", "telegram_bot_api", "mock"]
EstadoCadastro = Literal["ativa", "desativada", "removida"]


class ContaEntrada(OperacaoEntrada):
    operacao_id: UUID
    nome: str = Field(min_length=1, max_length=120)
    plataforma: Plataforma
    provedor: Provedor

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, value):
        value = unicodedata.normalize("NFC", value.strip())
        if not value or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("Informe um nome válido.")
        return value

    @model_validator(mode="after")
    def combinacao(self):
        if (self.provedor == "telegram_bot_api") != (self.plataforma == "telegram"):
            raise ValueError("Combinação inválida.")
        return self


class RevisaoEntrada(OperacaoEntrada):
    operacao_id: UUID
    versao: int = Field(ge=1)


class ContaEdicao(RevisaoEntrada):
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    ativo: bool | None = None
    remover: Literal[True] | None = None

    @field_validator("nome")
    @classmethod
    def nome_valido(cls, value):
        return ContaEntrada.nome_valido(value) if value is not None else value

    @model_validator(mode="after")
    def alteracao(self):
        fields = self.model_fields_set - {"operacao_id", "versao"}
        if not fields or any(getattr(self, key) is None for key in fields):
            raise ValueError("Informe uma alteração válida.")
        if "remover" in fields and len(fields) != 1:
            raise ValueError("Remoção deve ser uma operação separada.")
        return self


class CredencialEntrada(RevisaoEntrada):
    credencial: SecretStr = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("credencial")
    @classmethod
    def credencial_valida(cls, value):
        # Limite estrutural local; não comprova validade de token no provedor.
        if any(not 33 <= ord(c) <= 126 for c in value.get_secret_value()):
            raise ValueError("Credencial inválida.")
        return value
