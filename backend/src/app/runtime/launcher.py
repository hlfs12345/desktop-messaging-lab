"""Servidor de uma instância; IPC não contém regras de persistência ou negócio."""

import asyncio
import contextlib
import os
import socket
import struct
import sys
import threading

import uvicorn

from app.core.instance_auth import InstanceAuthorization
from app.main import create_app
from app.persistencia.errors import ConfigurationError, InstanceInUseError
from app.runtime.control_protocol import (
    Code,
    EndOfPipe,
    Kind,
    ProtocolError,
    read_frame,
    write_frame,
)
from app.runtime.integrated_lifetime import IntegratedLifetime


class IntegratedServer(uvicorn.Server):
    def __init__(self, config, lifetime):
        super().__init__(config)
        self.lifetime = lifetime
        self.shutdown_started = False

    def handle_exit(self, sig, frame):
        self.lifetime.close()
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets=None):
        self.shutdown_started = True
        self.lifetime.close()
        await super().shutdown(sockets=sockets)


class ControlInput:
    def read(self, size: int) -> bytes:
        # Sem BufferedReader: não manter seu lock na finalização do Python.
        return os.read(sys.stdin.fileno(), size)


async def serve(authorization: InstanceAuthorization, port: int) -> int:
    output = sys.stdout.buffer
    execution_id = authorization.execution_id
    lifetime = IntegratedLifetime(execution_id)

    def emit(kind: Kind, suffix: bytes = b"") -> None:
        write_frame(output, kind, execution_id + suffix)

    application = create_app(
        authorization=authorization, preparing=lambda: emit(Kind.PREPARING), lifetime=lifetime
    )
    server = IntegratedServer(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            workers=1,
            loop="asyncio",
            http="h11",
            proxy_headers=False,
            access_log=False,
            server_header=False,
            lifespan="on",
            timeout_graceful_shutdown=8,
            log_level="error",
        ),
        lifetime,
    )
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    protocol_failed = asyncio.Event()

    def command_stop(failed: bool) -> None:
        if failed:
            protocol_failed.set()
        stopped.set()
        server.should_exit = True

    def receive_commands() -> None:
        requested = False
        quiesced = False
        try:
            while True:
                kind, payload = read_frame(ControlInput())
                if payload[:16] != execution_id or requested:
                    raise ProtocolError("Comando inesperado.")
                if kind == Kind.LIFETIME:
                    if quiesced:
                        raise ProtocolError("Inicializacao fora de ordem.")
                    lifetime.attach(payload[16:])
                    continue
                if kind == Kind.QUIESCE:
                    if quiesced:
                        raise ProtocolError("Fechamento duplicado.")
                    quiesced = True
                    lifetime.close()
                    continue
                if kind != Kind.STOP or payload != execution_id or requested:
                    raise ProtocolError("Comando inesperado.")
                requested = True
                lifetime.close()  # Na thread do pipe, antes de agendar o event loop.
                loop.call_soon_threadsafe(command_stop, False)
        except EndOfPipe as error:
            lifetime.close()
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(command_stop, not requested or error.partial)
        except ProtocolError, OSError:
            lifetime.close()
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(command_stop, True)

    threading.Thread(target=receive_commands, daemon=True, name="edisk-control").start()
    emit(Kind.HELLO, struct.pack("<I", os.getpid()))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    async def announce_ready() -> None:
        while not server.started:
            await asyncio.sleep(0.01)
        if not stopped.is_set():
            try:
                emit(Kind.READY, struct.pack("<H", sock.getsockname()[1]))
            except OSError, ProtocolError:
                protocol_failed.set()
                server.should_exit = True

    announcement: asyncio.Task | None = None
    try:
        if getattr(sys, "frozen", False):
            from app.runtime.bundle_integrity import verify_frozen_bundle

            try:
                verify_frozen_bundle()
            except ConfigurationError:
                emit(Kind.ERROR, struct.pack("<H", Code.CONFIGURATION))
                return 1
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        sock.setblocking(False)
        announcement = asyncio.create_task(announce_ready())
        await server.serve(sockets=[sock])
        # Uvicorn retorna depois de startup se should_exit ja estava marcado;
        # nesse caminho ele nao chama shutdown/lifespan por conta propria.
        if server.started and not server.shutdown_started and not server.force_exit:
            await server.shutdown(sockets=[sock])
        if (
            getattr(application.state, "shutdown_unconfirmed", False)
            or getattr(server.lifespan, "shutdown_failed", False)
            or server.force_exit
            or (server.started and not getattr(application.state, "shutdown_complete", False))
        ):
            emit(Kind.ERROR, struct.pack("<H", Code.PERSISTENCE))
            return 1
        if protocol_failed.is_set():
            emit(Kind.ERROR, struct.pack("<H", Code.PROTOCOL))
            return 1
        if not server.started or not stopped.is_set():
            emit(Kind.ERROR, struct.pack("<H", Code.STARTUP))
            return 1
        emit(Kind.STOPPED)
        return 0
    except Exception, SystemExit:
        failure = getattr(application.state, "startup_error", None)
        code = (
            Code.INSTANCE_IN_USE
            if failure is InstanceInUseError
            else Code.CONFIGURATION
            if failure is ConfigurationError
            else Code.PERSISTENCE
            if failure
            else Code.STARTUP
        )
        with contextlib.suppress(OSError, ProtocolError):
            emit(Kind.ERROR, struct.pack("<H", code))
        return 1
    finally:
        lifetime.dispose()
        if announcement is not None:
            announcement.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError, ProtocolError):
                await announcement
        sock.close()


def run(port: int = 0) -> int:
    if sys.stdin.isatty():
        print("Inicie o backend pelo Messaging Lab integrado.", file=sys.stderr)
        return 1
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        kind, payload = read_frame(ControlInput())
        if kind != Kind.INIT:
            raise ProtocolError("Inicialização privada ausente.")
        from app.runtime.installed_coordination import verify_installed_coordination

        verify_installed_coordination()
        authorization = InstanceAuthorization(payload[:16], payload[16:])
        return asyncio.run(serve(authorization, port))
    except ProtocolError, OSError, ValueError:
        print("Falha no canal privado de inicialização.", file=sys.stderr)
        return 1
