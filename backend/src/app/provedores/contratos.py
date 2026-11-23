"""Contrato transitório de texto; sem SDK, credenciais ou autorização de campanha."""

from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class Provedor(StrEnum):
    MOCK = "mock"
    QR_CANDIDATO = "qr_candidato"
    META_CLOUD = "meta_cloud"
    TELEGRAM_BOT_API = "telegram_bot_api"


class Plataforma(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class ContextoProvedor:
    provedor: Provedor
    plataforma: Plataforma

    def __post_init__(self):
        if not isinstance(self.provedor, Provedor) or not isinstance(self.plataforma, Plataforma):
            raise ValueError("Contexto de provedor inválido.")
        if self.provedor == Provedor.TELEGRAM_BOT_API:
            compativel = self.plataforma == Plataforma.TELEGRAM
        else:
            compativel = self.provedor == Provedor.MOCK or self.plataforma == Plataforma.WHATSAPP
        if not compativel:
            raise ValueError("Provedor e plataforma incompatíveis.")


@dataclass(frozen=True, slots=True)
class CapacidadesProvedor:
    texto: bool
    simulacao: bool
    entrega: bool = False
    leitura: bool = False
    botoes: bool = False
    anexos: bool = False
    conexao_externa_verificada: bool = False

    def __post_init__(self):
        if any(type(getattr(self, item.name)) is not bool for item in fields(self)):
            raise ValueError("Capacidades de provedor inválidas.")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class EntradaTentativa:
    tentativa_id: UUID
    contexto: ContextoProvedor
    destino: str = field(repr=False)
    texto: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.tentativa_id, UUID) or self.tentativa_id.int == 0:
            raise ValueError("Identificador de tentativa inválido.")
        if not isinstance(self.contexto, ContextoProvedor):
            raise ValueError("Contexto de tentativa inválido.")
        # Validação estrutural apenas: não infere titularidade, existência ou permissão.
        if (
            not isinstance(self.destino, str)
            or not 1 <= len(self.destino) <= 256
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.destino)
        ):
            raise ValueError("Destino normalizado inválido.")
        if (
            not isinstance(self.texto, str)
            or not 1 <= len(self.texto) <= 65536
            or not self.texto.strip()
        ):
            raise ValueError("Texto de tentativa inválido.")
        try:
            tamanho_destino = len(self.destino.encode("utf-8"))
            tamanho_texto = len(self.texto.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Conteúdo de tentativa não é UTF-8 válido.") from None
        if tamanho_destino > 256 or tamanho_texto > 65536:
            raise ValueError("Conteúdo de tentativa excede o limite permitido.")


class EstadoResultado(StrEnum):
    ACEITA_PROVEDOR = "aceita_provedor"
    FALHOU = "falhou"
    DESCONHECIDA = "desconhecida"


class CodigoResultado(StrEnum):
    ACEITA = "aceita"
    FALHA = "falha"
    SEM_CONFIRMACAO = "sem_confirmacao"


@dataclass(frozen=True, slots=True)
class ResultadoTentativa:
    tentativa_id: UUID
    contexto: ContextoProvedor
    estado: EstadoResultado
    codigo: CodigoResultado

    def __post_init__(self):
        if not isinstance(self.tentativa_id, UUID) or self.tentativa_id.int == 0:
            raise ValueError("Identificador de resultado inválido.")
        if not isinstance(self.contexto, ContextoProvedor):
            raise ValueError("Contexto de resultado inválido.")
        if not isinstance(self.estado, EstadoResultado) or not isinstance(
            self.codigo, CodigoResultado
        ):
            raise ValueError("Estado ou código de resultado inválido.")
        if (self.estado, self.codigo) not in (
            (EstadoResultado.ACEITA_PROVEDOR, CodigoResultado.ACEITA),
            (EstadoResultado.FALHOU, CodigoResultado.FALHA),
            (EstadoResultado.DESCONHECIDA, CodigoResultado.SEM_CONFIRMACAO),
        ):
            raise ValueError("Estado e código de resultado incompatíveis.")


class ProvedorTexto(Protocol):
    @property
    def capacidades(self) -> CapacidadesProvedor: ...

    def executar(self, entrada: EntradaTentativa) -> ResultadoTentativa: ...
