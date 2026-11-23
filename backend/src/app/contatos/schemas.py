"""Entradas estritas sem exposição de valores nos erros."""

from pydantic import BaseModel, ConfigDict, Field


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class TelefoneEntrada(Entrada):
    telefone: str = Field(max_length=80)
    pais: str | None = Field(default=None, max_length=2)


class ContatoEntrada(TelefoneEntrada):
    nome: str | None = Field(default=None, max_length=120)
    observacoes: str | None = Field(default=None, max_length=2000)


class ContatoEdicao(Entrada):
    versao: int = Field(ge=1)
    nome: str | None = Field(default=None, max_length=120)
    observacoes: str | None = Field(default=None, max_length=2000)
    ativo: bool | None = None


class Arquivamento(Entrada):
    versao: int = Field(ge=1)
    ativo: bool = False


class ListaEntrada(Entrada):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str | None = Field(default=None, max_length=2000)


class ListaEdicao(Entrada):
    versao: int = Field(ge=1)
    nome: str | None = Field(default=None, max_length=120)
    descricao: str | None = Field(default=None, max_length=2000)
    ativo: bool | None = None
