"""Limite antes do parser JSON, inclusive em transferências sem Content-Length."""

import asyncio

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.errors import AuthError
from app.auth.service import TOKEN_PATTERN


async def auth_error(_request: Request, error: AuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if error.retry_after:
        headers["Retry-After"] = str(error.retry_after)
    return JSONResponse(
        {"codigo": error.code, "detail": str(error)}, status_code=error.status, headers=headers
    )


async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    allowed = {"nome", "login", "senha"}
    fields = sorted(
        {str(e["loc"][-1]) for e in error.errors() if e.get("loc") and e["loc"][-1] in allowed}
    )
    return JSONResponse(
        {"codigo": "validacao", "detail": "Confira os campos informados.", "campos": fields},
        status_code=422,
        headers={"Cache-Control": "no-store"},
    )


class AuthBoundaryMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/auth/"):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, detail: str) -> None:
            await JSONResponse(
                {"codigo": code, "detail": detail},
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        values = [v for k, v in scope.get("headers", []) if k == b"x-edisk-session"]
        if values and (
            len(values) != 1
            or TOKEN_PATTERN.fullmatch(values[0].decode("ascii", errors="replace")) is None
        ):
            await reject(401, "sessao_invalida", "Sua sessão terminou. Entre novamente.")
            return
        lengths = [v for k, v in scope.get("headers", []) if k == b"content-length"]
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10)):
            await reject(422, "validacao", "Requisição inválida.")
            return
        if lengths and int(lengths[0]) > 8192:
            await reject(413, "corpo_excessivo", "Requisição excede o tamanho permitido.")
            return
        body = bytearray()
        try:
            async with asyncio.timeout(3):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(chunk) > 8192 - len(body):
                        await reject(
                            413, "corpo_excessivo", "Requisição excede o tamanho permitido."
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(422, "validacao", "Requisição incompleta.")
            return
        sent = False

        async def buffered() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        started = False

        async def track(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, buffered, track)
        except Exception:
            if started:
                raise
            # Não propagar exceções com corpos, parâmetros ou objetos secretos
            # para logs do servidor. Erros de domínio têm handler próprio.
            await reject(500, "erro_interno", "Não foi possível concluir a operação.")
