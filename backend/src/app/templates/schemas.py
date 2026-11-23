"""Entradas limitadas; sem linguagem de execução ou dados de contatos."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.consentimentos.schemas import Categoria, OperacaoEntrada
from app.contatos.schemas import Entrada

Plataforma = Literal["whatsapp", "telegram"]


class ConteudoEntrada(Entrada):
    plataforma: Plataforma
    conteudo: str = Field(min_length=1, max_length=4000, repr=False)


class TemplateEntrada(OperacaoEntrada, ConteudoEntrada):
    # UUID convertido explicitamente pelo contrato de operações existente.
    operacao_id: UUID
    nome: str = Field(min_length=1, max_length=120)
    descricao: str | None = Field(default=None, max_length=2000, repr=False)
    categoria: Categoria

    @field_validator("nome")
    @classmethod
    def nome_preenchido(cls, value):
        if not value.strip():
            raise ValueError("Informe o nome.")
        return value.strip()


class VersaoEntrada(OperacaoEntrada, ConteudoEntrada):
    operacao_id: UUID


class TemplateEdicao(OperacaoEntrada):
    operacao_id: UUID
    versao: int = Field(ge=1)
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    descricao: str | None = Field(default=None, max_length=2000, repr=False)
    ativo: Literal[False] | None = None

    @model_validator(mode="after")
    def alteracao(self):
        if not self.model_fields_set.intersection({"nome", "descricao", "ativo"}):
            raise ValueError("Informe a alteração.")
        if "nome" in self.model_fields_set:
            if self.nome is None or not self.nome.strip():
                raise ValueError("Informe o nome.")
            self.nome = self.nome.strip()
        if "ativo" in self.model_fields_set and self.ativo is not False:
            raise ValueError("Arquivamento inválido.")
        return self


class PreviaEntrada(ConteudoEntrada):
    valores: dict[Literal["nome"], str] = Field(default_factory=dict, repr=False)

    @field_validator("valores")
    @classmethod
    def valores_limitados(cls, value):
        if any(not v.strip() or len(v) > 120 for v in value.values()):
            raise ValueError("Informe um nome fictício de até 120 caracteres.")
        return value
