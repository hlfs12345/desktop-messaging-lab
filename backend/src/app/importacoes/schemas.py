"""Configuração explícita e limitada de importação."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.contatos.schemas import Entrada


class NovaImportacao(Entrada):
    nome_arquivo: str = Field(min_length=1, max_length=180)


class ConfiguracaoImportacao(Entrada):
    aba: str | None = Field(default=None, max_length=100)
    cabecalho: int | None = Field(default=1, ge=1, le=20000)
    codificacao: Literal["utf-8-sig", "cp1252"] = "utf-8-sig"
    delimitador: Literal[",", ";", "\t"] = ";"
    coluna_telefone: int = Field(ge=0, le=99)
    coluna_nome: int | None = Field(default=None, ge=0, le=99)
    coluna_observacoes: int | None = Field(default=None, ge=0, le=99)
    coluna_pais: int | None = Field(default=None, ge=0, le=99)
    pais: str | None = Field(default=None, min_length=2, max_length=2)
    politica: Literal["preservar", "preencher", "substituir"] = "preservar"
    campos_atualizar: list[Literal["nome", "observacoes"]] = Field(
        default_factory=list, max_length=2
    )
    listas: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("listas")
    @classmethod
    def lista_ids(cls, value):
        return sorted({str(UUID(v)) for v in value})
