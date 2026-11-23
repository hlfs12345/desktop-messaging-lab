"""Protocolo v1: cabeçalho <4sBBH, payload <=512; nunca registrar frames."""

import struct
from enum import IntEnum
from typing import BinaryIO

MAGIC = b"ED5P"
VERSION = 1
LIMIT = 512
HEADER = struct.Struct("<4sBBH")


class Kind(IntEnum):
    INIT = 1
    HELLO = 2
    PREPARING = 3
    READY = 4
    ERROR = 5
    STOP = 6
    STOPPED = 7
    LIFETIME = 8
    QUIESCE = 9


SIZES = {
    Kind.INIT: 48,
    Kind.HELLO: 20,
    Kind.PREPARING: 16,
    Kind.READY: 18,
    Kind.ERROR: 18,
    Kind.STOP: 16,
    Kind.STOPPED: 16,
    Kind.LIFETIME: 32,
    Kind.QUIESCE: 16,
}


class Code(IntEnum):
    PROTOCOL = 1
    CONFIGURATION = 2
    INSTANCE_IN_USE = 3
    PERSISTENCE = 4
    STARTUP = 5


class ProtocolError(Exception):
    """Erro fixo; não inclui conteúdo recebido."""


class EndOfPipe(ProtocolError):
    def __init__(self, partial: bool) -> None:
        super().__init__("Canal privado interrompido.")
        self.partial = partial


def _exact(stream: BinaryIO, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            raise EndOfPipe(partial=bool(result))
        result.extend(chunk)
    return bytes(result)


def read_frame(stream: BinaryIO) -> tuple[Kind, bytes]:
    magic, version, raw_kind, size = HEADER.unpack(_exact(stream, HEADER.size))
    try:
        kind = Kind(raw_kind)
    except ValueError:
        raise ProtocolError("Tipo de controle inválido.") from None
    if magic != MAGIC or version != VERSION or size > LIMIT or size != SIZES[kind]:
        raise ProtocolError("Cabeçalho de controle inválido.")
    try:
        payload = _exact(stream, size)
    except EndOfPipe:
        raise EndOfPipe(partial=True) from None
    return kind, payload


def write_frame(stream: BinaryIO, kind: Kind, payload: bytes) -> None:
    if len(payload) != SIZES[kind]:
        raise ProtocolError("Tamanho de controle inválido.")
    frame = HEADER.pack(MAGIC, VERSION, kind, len(payload)) + payload
    view = memoryview(frame)
    while view:
        count = stream.write(view)
        if not count:
            raise ProtocolError("Canal privado indisponível.")
        view = view[count:]
    stream.flush()
