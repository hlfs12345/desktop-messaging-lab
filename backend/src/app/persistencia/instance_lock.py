"""Exclusividade por banco no Windows, mantida por um descritor não herdável."""

import errno
import msvcrt
import os
from pathlib import Path

from app.persistencia.errors import InstanceInUseError, LockAccessError


class InstanceLock:
    def __init__(self, database_path: Path) -> None:
        self.path = database_path.resolve().with_suffix(".instance.lock")
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise InstanceInUseError("O bloqueio desta instância já está ativo.")
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
        except OSError:
            raise LockAccessError("Não foi possível abrir o controle de instância.") from None
        try:
            # É permitido bloquear além do EOF; não é necessário escrever nem truncar.
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            os.close(descriptor)
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise InstanceInUseError("Outra instância já está utilizando este banco.") from None
            raise LockAccessError("Não foi possível bloquear o controle de instância.") from None
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)

    @property
    def ativo(self) -> bool:
        return self._descriptor is not None
