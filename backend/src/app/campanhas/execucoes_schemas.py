"""Comandos imutaveis de execucao; confirmacoes estritas e sem campos extras."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contas_envio.schemas import RevisaoEntrada


class IniciarExecucaoEntrada(RevisaoEntrada):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    modo: Literal["mock", "telegram"] = "mock"
    autorizacao_id: UUID | None = None
    versao_autorizacao: int | None = Field(default=None, ge=1)
    impressao: str = Field(pattern=r"^[0-9a-f]{64}$")
    resumo_configuracao: str = Field(pattern=r"^[0-9a-f]{64}$")
    marcador_inicio: str = Field(pattern=r"^inicio-v1\.[0-9a-f]{64}$", repr=False)
    confirmo_inicio: bool

    @field_validator("confirmo_inicio")
    @classmethod
    def confirmacao_explicita(cls, value):
        if value is not True:
            raise ValueError("Confirme explicitamente o inicio da execucao.")
        return value

    @model_validator(mode="after")
    def vinculos(self):
        if self.modo == "mock" and (
            self.autorizacao_id is None or self.versao_autorizacao is None
        ):
            raise ValueError("Autorizacao mock obrigatoria.")
        if self.modo == "telegram" and (
            self.autorizacao_id is not None or self.versao_autorizacao is not None
        ):
            raise ValueError("Autorizacao Telegram sera criada com a execucao.")
        return self


class PausarExecucaoEntrada(RevisaoEntrada):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")


class RetomarExecucaoEntrada(RevisaoEntrada):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")

    modo: Literal["mock", "telegram"] = "mock"
    preparacao_id: UUID
    autorizacao_id: UUID
    versao_autorizacao: int = Field(ge=1)
    geracao: int = Field(ge=1)
    impressao: str = Field(pattern=r"^[0-9a-f]{64}$")
    resumo_configuracao: str = Field(pattern=r"^[0-9a-f]{64}$")
    marcador_retomada: str = Field(pattern=r"^ativar-v1\.[0-9a-f]{64}$", repr=False)
    confirmo_retomada: bool

    @field_validator("confirmo_retomada")
    @classmethod
    def confirmacao_explicita(cls, value):
        if value is not True:
            raise ValueError("Confirme explicitamente a retomada da execucao.")
        return value


class OperacionalSaida(BaseModel):
    processamento_disponivel: bool
    admitida: bool
    worker_ativo: bool
    recuperacao_disponivel: bool
    encerrando: bool


class ExecucaoSaida(BaseModel):
    id: str
    campanha_id: str
    preparacao_id: str
    autorizacao_id: str
    estado: Literal[
        "executando", "pausando", "pausada", "concluida", "concluida_com_falhas", "interrompida"
    ]
    versao: int
    geracao: int
    criado_em: datetime
    atualizado_em: datetime
    contagens: dict[str, int]
    modo: Literal["mock", "telegram"]
    operacional: OperacionalSaida
    pausa_solicitada: bool
    pausa_efetivada: bool
    simulacao: bool
    aviso: str
    validade_atual: Literal["nao_avaliada"]


class ItemExecucaoSaida(BaseModel):
    id: str
    execucao_id: str
    ordem: int
    estado: Literal[
        "pendente",
        "reservado",
        "processando",
        "aceita_provedor",
        "falhou",
        "desconhecida",
        "ignorado",
        "interrompido",
    ]
    versao: int
    motivo_codigo: Literal[
        "impedimento_atual", "consentimento", "sincronizacao", "encerramento"
    ] | None
    atualizado_em: datetime


class TentativaSaida(BaseModel):
    id: str
    execucao_id: str
    item_id: str
    numero: int
    estado: Literal[
        "reservada", "iniciada", "interrompida", "aceita_provedor", "falhou", "desconhecida"
    ]
    versao: int
    geracao: int
    reservada_em: datetime
    invocacao_em: datetime | None
    finalizada_em: datetime | None
    atualizado_em: datetime
    codigo_resultado: str | None
    message_id: int | None
    retry_after: int | None


class PaginaExecucaoSaida[T](BaseModel):
    itens: list[T]
    total: int
    pagina: int
    limite: int
    simulacao: bool
    aviso: str


class AtivacaoSaida(BaseModel):
    solicitada: bool
    admissao_confirmada: bool
    erro_codigo: Literal["ativacao_indisponivel"] | None


class ComandoExecucaoSaida(BaseModel):
    recibo: dict[str, Any]
    replay: bool
    ativacao: AtivacaoSaida
    atual: ExecucaoSaida
    simulacao: bool
    aviso: str
    modo: Literal["mock", "telegram"] = "mock"
