"""Capacidade privada do supervisor: evento Windows, somente SYNCHRONIZE no filho."""

import ctypes
import sys
from threading import Event, RLock

from app.contatos.errors import NegocioError
from app.runtime.control_protocol import ProtocolError


class IntegratedLifetime:
    def __init__(self, execution_id):
        self.execution_id = execution_id
        self.closed = Event()
        self._lock = RLock()
        self._handle = None
        self._kernel = None
        self._callbacks = []

    def attach(self, capability):
        if sys.platform != "win32" or len(capability) != 16:
            raise ProtocolError("Contexto integrado invalido.")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
        kernel.OpenEventW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.IsProcessInJob.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        member = ctypes.c_int()
        if (
            not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(member))
            or not member.value
        ):
            raise ProtocolError("Contexto integrado sem contencao.")
        name = "Local\\eDisk-close-" + self.execution_id.hex() + "-" + capability.hex()
        with self._lock:
            if self._handle is not None:
                raise ProtocolError("Contexto integrado duplicado.")
            handle = kernel.OpenEventW(0x100000, False, name)
            if not handle:
                raise ProtocolError("Contexto integrado indisponivel.")
            self._kernel, self._handle = kernel, handle
        if not self.is_open():
            self.close()

    def is_open(self):
        with self._lock:
            return (
                not self.closed.is_set()
                and self._handle is not None
                and self._kernel.WaitForSingleObject(self._handle, 0) == 0x102
            )

    def require_open(self):
        if not self.is_open():
            self.close()
            raise NegocioError("aplicacao_fechando", "Processamento integrado indisponivel.", 409)

    def subscribe(self, callback):
        with self._lock:
            self._callbacks.append(callback)
            closed = self.closed.is_set()
        if closed:
            callback()

    def close(self):
        with self._lock:
            if self.closed.is_set():
                return
            self.closed.set()
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback()

    def dispose(self):
        self.close()
        with self._lock:
            if self._handle is not None:
                self._kernel.CloseHandle(self._handle)
                self._handle = None
