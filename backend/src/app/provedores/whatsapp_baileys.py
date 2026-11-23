"""Ciclo de vida e texto unitario no sidecar Baileys, isolados por conta."""

import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock, Thread
from uuid import uuid4

from app.contatos.errors import NegocioError

_TIMEOUT = 8.0
_MAX_LINE = 512 * 1024


@dataclass(frozen=True)
class ResultadoEnvioWhatsapp:
    estado: str
    codigo: str
    message_id: str | None = None


class _ProcessoConta:
    def __init__(self, conta_id: str, sidecar: Path):
        self.conta_id = conta_id
        self.sidecar = sidecar
        self.capacidade = secrets.token_hex(32)
        self._condition = Condition(RLock())
        self._command_lock = RLock()
        self._responses = {}
        self._abandoned = set()
        self._status = None
        self._failed = False
        self._process = self._start()
        Thread(target=self._read, daemon=True, name=f"whatsapp-{conta_id[:8]}").start()
        with self._condition:
            if not self._condition.wait_for(
                lambda: self._status is not None or self._failed, timeout=_TIMEOUT
            ):
                self._failed = True
        if self._failed:
            self.close(force=True)
            raise NegocioError(
                "whatsapp_indisponivel",
                "O componente local do WhatsApp não iniciou.",
                503,
            )

    def _start(self):
        node = shutil.which("node.exe") or shutil.which("node")
        entrypoint = self.sidecar / "index.mjs"
        if not node or not entrypoint.is_file():
            raise NegocioError(
                "whatsapp_indisponivel",
                "O componente local do WhatsApp não está disponível.",
                503,
            )
        environment = {
            name: os.environ[name]
            for name in ("SystemRoot", "WINDIR", "LOCALAPPDATA", "TEMP", "TMP")
            if name in os.environ
        }
        environment.update(
            {
                "EDISK_PYTHON": sys.executable,
                "EDISK_WHATSAPP_ACCOUNT_ID": self.conta_id,
                "EDISK_WHATSAPP_IPC_TOKEN": self.capacidade,
            }
        )
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            return subprocess.Popen(
                [node, str(entrypoint)],
                cwd=self.sidecar,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                creationflags=flags,
            )
        except OSError:
            raise NegocioError(
                "whatsapp_indisponivel",
                "O componente local do WhatsApp não iniciou.",
                503,
            ) from None

    def _read(self):
        try:
            for line in self._process.stdout:
                if len(line) > _MAX_LINE:
                    break
                try:
                    data = json.loads(line)
                    status = self._validate(data)
                except (TypeError, ValueError, json.JSONDecodeError):
                    break
                with self._condition:
                    self._status = status
                    request_id = data.get("requisicao_id")
                    if isinstance(request_id, str):
                        if request_id in self._abandoned:
                            self._abandoned.remove(request_id)
                        else:
                            self._responses[request_id] = status
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._failed = True
                self._condition.notify_all()

    def _validate(self, data):
        if (
            not isinstance(data, dict)
            or data.get("capacidade") != self.capacidade
            or data.get("evento") != "status"
            or not isinstance(data.get("estado"), str)
            or not isinstance(data.get("sessao_persistida"), bool)
        ):
            raise ValueError
        matrix = data.get("qr")
        if matrix is not None:
            if (
                not isinstance(matrix, list)
                or not 21 <= len(matrix) <= 177
                or any(
                    not isinstance(row, list)
                    or len(row) != len(matrix)
                    or any(type(cell) is not bool for cell in row)
                    for row in matrix
                )
            ):
                raise ValueError
        result = {
            "estado": data["estado"],
            "sessao_persistida": data["sessao_persistida"],
            "qr": matrix,
        }
        envio = data.get("resultado_envio")
        if envio is not None:
            if (
                not isinstance(envio, dict)
                or envio.get("estado") not in {"confirmada", "falhou", "desconhecida"}
                or not isinstance(envio.get("codigo"), str)
                or not 1 <= len(envio["codigo"]) <= 80
                or (
                    envio.get("message_id") is not None
                    and (
                        not isinstance(envio["message_id"], str)
                        or not 1 <= len(envio["message_id"]) <= 256
                    )
                )
                or (envio["estado"] == "confirmada") != (envio.get("message_id") is not None)
            ):
                raise ValueError
            result["resultado_envio"] = envio
        return result

    def command(self, command: str, payload=None, *, timeout=_TIMEOUT):
        with self._command_lock:
            with self._condition:
                if self._failed or self._process.poll() is not None:
                    raise NegocioError(
                        "whatsapp_indisponivel",
                        "O componente local do WhatsApp foi encerrado.",
                        503,
                    )
                request_id = str(uuid4())
                raw = json.dumps(
                    {
                        "capacidade": self.capacidade,
                        "requisicao_id": request_id,
                        "comando": command,
                        **(payload or {}),
                    },
                    separators=(",", ":"),
                )
                try:
                    self._process.stdin.write(raw + "\n")
                    self._process.stdin.flush()
                except (BrokenPipeError, OSError):
                    self._failed = True
                    raise NegocioError(
                        "whatsapp_indisponivel",
                        "O componente local do WhatsApp não respondeu.",
                        503,
                    ) from None
                if not self._condition.wait_for(
                    lambda: request_id in self._responses or self._failed,
                    timeout=timeout,
                ):
                    self._abandoned.add(request_id)
                    raise NegocioError(
                        "whatsapp_timeout",
                        "O componente local do WhatsApp não respondeu no prazo.",
                        504,
                    )
                response = self._responses.pop(request_id, None)
                if response is None:
                    raise NegocioError(
                        "whatsapp_indisponivel",
                        "O componente local do WhatsApp foi encerrado.",
                        503,
                    )
                return response

    def close(self, *, force=False):
        if not force and self._process.poll() is None:
            try:
                self.command("exit", timeout=2)
                self._process.wait(timeout=2)
            except (NegocioError, OSError, subprocess.TimeoutExpired):
                force = True
        if force and self._process.poll() is None:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass


class WhatsappBaileys:
    def __init__(self, sidecar: Path | None = None):
        self.sidecar = sidecar or Path(__file__).resolve().parents[4] / "poc" / "whatsapp-baileys"
        self._lock = RLock()
        self._processes = {}
        self._closed = False

    def _process(self, conta_id):
        with self._lock:
            if self._closed:
                raise NegocioError(
                    "aplicacao_fechando", "O aplicativo está sendo encerrado.", 409
                )
            process = self._processes.get(conta_id)
            if process is None or process._process.poll() is not None:
                if process is not None:
                    process.close(force=True)
                process = _ProcessoConta(conta_id, self.sidecar)
                self._processes[conta_id] = process
            return process

    def status(self, conta_id):
        return self._process(conta_id).command("status")

    def conectar(self, conta_id):
        return self._process(conta_id).command("connect")

    def desconectar(self, conta_id):
        return self._process(conta_id).command("disconnect")

    def desvincular(self, conta_id):
        return self._process(conta_id).command("unlink")

    def enviar(self, conta_id, destino_e164, texto, tentativa_id):
        response = self._process(conta_id).command(
            "send_text",
            {
                "destino_e164": destino_e164,
                "texto": texto,
                "tentativa_id": tentativa_id,
            },
        )
        result = response.get("resultado_envio")
        if result is None:
            return ResultadoEnvioWhatsapp("desconhecida", "sem_confirmacao")
        return ResultadoEnvioWhatsapp(result["estado"], result["codigo"], result.get("message_id"))

    def suspender(self, conta_id):
        with self._lock:
            process = self._processes.get(conta_id)
        if process is None:
            return
        try:
            process.command("disconnect")
        except NegocioError:
            process.close(force=True)

    def suspender_todos(self):
        with self._lock:
            identifiers = tuple(self._processes)
        for identifier in identifiers:
            self.suspender(identifier)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            processes = tuple(self._processes.values())
            self._processes.clear()
        for process in processes:
            process.close()
