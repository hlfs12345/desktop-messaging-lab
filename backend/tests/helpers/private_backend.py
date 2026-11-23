"""Supervisor de testes explícito; somente dados temporários e segredos em memória."""

import contextlib
import ctypes
import os
import queue
import secrets
import struct
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from app.core.instance_auth import InstanceAuthorization
from app.runtime.control_protocol import Kind, ProtocolError, read_frame, write_frame


class PrivateBackend:
    def __init__(
        self, root: Path, *, cwd: Path | None = None, port: int = 0, executable: Path | None = None
    ) -> None:
        self.authorization = InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))
        self.events: queue.Queue = queue.Queue()
        self.diagnostics = bytearray()
        self.process = subprocess.Popen(
            (
                [str(executable), "--port", str(port)]
                if executable
                else [sys.executable, "-u", "-B", "-m", "app", "--port", str(port)]
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, "EDISK_ENV": "test", "EDISK_TEST_ROOT": str(root)},
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.logs = threading.Thread(target=self._logs, daemon=True)
        self.reader.start()
        self.logs.start()
        write_frame(
            self.process.stdin,
            Kind.INIT,
            self.authorization.execution_id + self.authorization.secret,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.authorization.token}"}

    def _read(self) -> None:
        try:
            while True:
                self.events.put(read_frame(self.process.stdout))
        except (ProtocolError, OSError) as error:
            self.events.put(error)

    def _logs(self) -> None:
        while chunk := self.process.stderr.read(1024):
            self.diagnostics.extend(chunk)

    def next(self, kind: Kind, timeout: float = 15) -> bytes:
        event = self.events.get(timeout=timeout)
        if isinstance(event, Exception):
            raise AssertionError("Canal de teste interrompido") from event
        actual, payload = event
        assert actual == kind, f"Esperado {kind.name}, recebido {actual.name}"
        assert payload[:16] == self.authorization.execution_id
        return payload[16:]

    def ready(self) -> str:
        self.backend_pid = struct.unpack("<I", self.next(Kind.HELLO))[0]
        assert self.backend_pid > 0
        self.next(Kind.PREPARING)
        port = struct.unpack("<H", self.next(Kind.READY))[0]
        assert 0 < port <= 65535
        return f"http://127.0.0.1:{port}"

    def kill_backend(self) -> None:
        # O redirector da .venv pode ter PID diferente do interpretador real.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100001, False, self.backend_pid)
        assert handle
        try:
            assert kernel.TerminateProcess(handle, 1)
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)

    def stop(self) -> None:
        write_frame(self.process.stdin, Kind.STOP, self.authorization.execution_id)
        self.next(Kind.STOPPED)
        assert self.process.wait(timeout=10) == 0

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            with contextlib.suppress(OSError):
                stream.close()
        self.reader.join(timeout=2)
        self.logs.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
