"""Simulador isolado: cenários por UUID, sem I/O, histórico ou payload retido."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from app.provedores.contratos import (
    CapacidadesProvedor,
    CodigoResultado,
    EntradaTentativa,
    EstadoResultado,
    Provedor,
    ResultadoTentativa,
)

VERSAO_CONTRATO_MOCK = "texto_v1"


class CenarioMock(StrEnum):
    ACEITA_PROVEDOR = "aceita_provedor"
    FALHOU = "falhou"
    DESCONHECIDA = "desconhecida"
    INTERRUPCAO = "interrupcao"
    EXCECAO = "excecao"


class InterrupcaoSimulada(RuntimeError):
    def __init__(self):
        super().__init__("Tentativa simulada interrompida; resultado não confirmado.")


class ExcecaoSimulada(RuntimeError):
    def __init__(self):
        super().__init__("Exceção simulada; resultado não confirmado.")


@dataclass(frozen=True, slots=True)
class MockProvedor:
    cenarios: Mapping[UUID, CenarioMock] = field(default_factory=dict, repr=False)
    padrao: CenarioMock = CenarioMock.ACEITA_PROVEDOR

    def __post_init__(self):
        if not isinstance(self.padrao, CenarioMock) or not isinstance(self.cenarios, Mapping):
            raise ValueError("Configuração do mock inválida.")
        copia = dict(self.cenarios)
        if any(
            not isinstance(identifier, UUID)
            or identifier.int == 0
            or not isinstance(cenario, CenarioMock)
            for identifier, cenario in copia.items()
        ):
            raise ValueError("Cenário do mock inválido.")
        object.__setattr__(self, "cenarios", MappingProxyType(copia))

    @property
    def capacidades(self) -> CapacidadesProvedor:
        return CapacidadesProvedor(texto=True, simulacao=True)

    def executar(self, entrada: EntradaTentativa) -> ResultadoTentativa:
        if not isinstance(entrada, EntradaTentativa):
            raise ValueError("Entrada de tentativa inválida.")
        if entrada.contexto.provedor != Provedor.MOCK:
            raise ValueError("O mock exige contexto de provedor mock.")
        cenario = self.cenarios.get(entrada.tentativa_id, self.padrao)
        match cenario:
            case CenarioMock.INTERRUPCAO:
                raise InterrupcaoSimulada
            case CenarioMock.EXCECAO:
                raise ExcecaoSimulada
            case CenarioMock.ACEITA_PROVEDOR:
                estado, codigo = EstadoResultado.ACEITA_PROVEDOR, CodigoResultado.ACEITA
            case CenarioMock.FALHOU:
                estado, codigo = EstadoResultado.FALHOU, CodigoResultado.FALHA
            case CenarioMock.DESCONHECIDA:
                estado, codigo = EstadoResultado.DESCONHECIDA, CodigoResultado.SEM_CONFIRMACAO
        return ResultadoTentativa(entrada.tentativa_id, entrada.contexto, estado, codigo)
