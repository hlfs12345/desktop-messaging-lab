"""Estados puros operacionais; nenhuma persistencia ou invocacao de provedor."""

from dataclasses import dataclass, fields
from enum import StrEnum


class EstadoExecucao(StrEnum):
    EXECUTANDO = "executando"
    PAUSANDO = "pausando"
    PAUSADA = "pausada"
    CONCLUIDA = "concluida"
    CONCLUIDA_COM_FALHAS = "concluida_com_falhas"
    INTERROMPIDA = "interrompida"


class EstadoItem(StrEnum):
    PENDENTE = "pendente"
    RESERVADO = "reservado"
    PROCESSANDO = "processando"
    ACEITA_PROVEDOR = "aceita_provedor"
    FALHOU = "falhou"
    DESCONHECIDA = "desconhecida"
    IGNORADO = "ignorado"
    INTERROMPIDO = "interrompido"


class EstadoTentativa(StrEnum):
    RESERVADA = "reservada"
    INICIADA = "iniciada"
    ACEITA_PROVEDOR = "aceita_provedor"
    FALHOU = "falhou"
    DESCONHECIDA = "desconhecida"
    INTERROMPIDA = "interrompida"


class TransicaoInvalida(ValueError):
    def __init__(self):
        super().__init__("Transicao de execucao invalida.")


@dataclass(frozen=True, slots=True)
class ResumoItens:
    pendentes: int = 0
    reservados: int = 0
    processando: int = 0
    aceitas_provedor: int = 0
    falhas: int = 0
    desconhecidas: int = 0
    ignorados: int = 0
    interrompidos: int = 0

    def __post_init__(self):
        if any(
            type(getattr(self, f.name)) is not int or getattr(self, f.name) < 0
            for f in fields(self)
        ):
            raise ValueError("Contagens de itens invalidas.")

    @property
    def total(self):
        return sum(getattr(self, f.name) for f in fields(self))

    @property
    def ativos(self):
        return self.reservados + self.processando

    @property
    def abertos(self):
        return self.pendentes + self.ativos


def transicionar_execucao(atual, destino, resumo):
    if (
        not isinstance(atual, EstadoExecucao)
        or not isinstance(destino, EstadoExecucao)
        or not isinstance(resumo, ResumoItens)
    ):
        raise TransicaoInvalida
    if not resumo.total:
        raise TransicaoInvalida
    if (atual, destino) in {
        (EstadoExecucao.EXECUTANDO, EstadoExecucao.PAUSANDO),
        (EstadoExecucao.PAUSADA, EstadoExecucao.EXECUTANDO),
    }:
        return destino
    if atual not in {EstadoExecucao.EXECUTANDO, EstadoExecucao.PAUSANDO, EstadoExecucao.PAUSADA}:
        raise TransicaoInvalida
    if destino == EstadoExecucao.PAUSADA and atual != EstadoExecucao.PAUSADA and not resumo.ativos:
        return destino
    if not resumo.abertos:
        if destino == EstadoExecucao.INTERROMPIDA:
            return destino
        esperado = (
            EstadoExecucao.CONCLUIDA_COM_FALHAS
            if resumo.falhas or resumo.desconhecidas or resumo.interrompidos
            else EstadoExecucao.CONCLUIDA
        )
        if destino == esperado:
            return destino
    raise TransicaoInvalida


def transicionar_item(atual, destino, execucao):
    if (
        not isinstance(atual, EstadoItem)
        or not isinstance(destino, EstadoItem)
        or not isinstance(execucao, EstadoExecucao)
    ):
        raise TransicaoInvalida
    if execucao not in {EstadoExecucao.EXECUTANDO, EstadoExecucao.PAUSANDO, EstadoExecucao.PAUSADA}:
        raise TransicaoInvalida
    if atual == EstadoItem.PENDENTE:
        if destino in {EstadoItem.IGNORADO, EstadoItem.INTERROMPIDO} or (
            destino == EstadoItem.RESERVADO and execucao == EstadoExecucao.EXECUTANDO
        ):
            return destino
    if atual == EstadoItem.RESERVADO:
        if destino == EstadoItem.PENDENTE or (
            destino == EstadoItem.PROCESSANDO and execucao == EstadoExecucao.EXECUTANDO
        ):
            return destino
    if (
        atual == EstadoItem.PROCESSANDO
        and execucao != EstadoExecucao.PAUSADA
        and destino in {EstadoItem.ACEITA_PROVEDOR, EstadoItem.FALHOU, EstadoItem.DESCONHECIDA}
    ):
        return destino
    raise TransicaoInvalida


def reservar_tentativa(execucao, item):
    return transicionar_item(item, EstadoItem.RESERVADO, execucao), EstadoTentativa.RESERVADA


def transicionar_tentativa(atual, destino, execucao):
    if (
        not isinstance(atual, EstadoTentativa)
        or not isinstance(destino, EstadoTentativa)
        or not isinstance(execucao, EstadoExecucao)
    ):
        raise TransicaoInvalida
    if execucao not in {EstadoExecucao.EXECUTANDO, EstadoExecucao.PAUSANDO}:
        raise TransicaoInvalida
    if atual == EstadoTentativa.RESERVADA and (
        destino == EstadoTentativa.INTERROMPIDA
        or (destino == EstadoTentativa.INICIADA and execucao == EstadoExecucao.EXECUTANDO)
    ):
        return destino
    if atual == EstadoTentativa.INICIADA and destino in {
        EstadoTentativa.ACEITA_PROVEDOR,
        EstadoTentativa.FALHOU,
        EstadoTentativa.DESCONHECIDA,
    }:
        return destino
    raise TransicaoInvalida
