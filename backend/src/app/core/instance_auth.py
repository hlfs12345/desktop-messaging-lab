"""Autorização temporária do processo; independente do futuro login local."""

import re
import secrets
from dataclasses import dataclass, field

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True)
class InstanceAuthorization:
    execution_id: bytes
    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.execution_id, bytes)
            or not isinstance(self.secret, bytes)
            or len(self.execution_id) != 16
            or len(self.secret) != 32
        ):
            raise ValueError("Contexto de instância inválido.")

    @property
    def token(self) -> str:
        return self.secret.hex()


class InstanceAuthMiddleware:
    def __init__(self, app: ASGIApp, authorization: InstanceAuthorization | None) -> None:
        self.app = app
        self.authorization = authorization

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        browser = any(key == b"origin" or key.startswith(b"sec-fetch-") for key, _ in headers)
        values = [value for key, value in headers if key == b"authorization"]
        valid = (
            self.authorization is not None
            and len(values) == 1
            and re.fullmatch(rb"Bearer [0-9a-f]{64}", values[0]) is not None
            and secrets.compare_digest(values[0][7:], self.authorization.token.encode("ascii"))
        )

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        if browser:
            await JSONResponse({"detail": "Acesso não permitido."}, status_code=403)(
                scope, receive, no_store
            )
        elif not valid:
            await JSONResponse(
                {"detail": "Não autorizado."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, no_store)
        else:
            await self.app(scope, receive, no_store)
