"""Autentica antes do parser e limita corpos de negócio, inclusive chunked."""

import asyncio

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.auth.dependencies import session_token
from app.auth.errors import AuthError
from app.auth.http_boundary import auth_error
from app.contas_envio.service import administrador
from app.contatos.errors import NegocioError


async def negocio_error(_request, error):
    return JSONResponse({"codigo": error.codigo, "detail": str(error)}, status_code=error.status)


class NegocioBoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].split("/")[1] not in {
            "contatos",
            "templates",
            "campanhas",
            "contas-envio",
            "listas",
            "importacoes",
        }:
            await self.app(scope, receive, send)
            return

        async def reject(status, code, message):
            await JSONResponse({"codigo": code, "detail": message}, status_code=status)(
                scope, receive, send
            )

        request = Request(scope)
        try:
            token = session_token(request)
            if scope["path"].split("/")[1] == "contas-envio" or (
                scope["path"].startswith("/campanhas/chaves/") and scope["method"] == "POST"
            ):
                await run_in_threadpool(administrador, scope["app"].state.auth, token)
            else:
                await run_in_threadpool(scope["app"].state.auth.current, token)
        except AuthError as error:
            await (await auth_error(request, error))(scope, receive, send)
            return
        except NegocioError as error:
            await reject(error.status, error.codigo, str(error))
            return
        upload = scope["method"] == "PUT" and scope["path"].endswith("/arquivo")
        limit = 10 * 1024 * 1024 if upload else 32768
        lengths = [v for k, v in scope.get("headers", []) if k == b"content-length"]
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10)):
            await reject(422, "validacao", "Requisição inválida.")
            return
        if lengths and int(lengths[0]) > limit:
            await reject(413, "corpo_excessivo", "Arquivo ou requisição excede o limite permitido.")
            return
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        await reject(
                            413,
                            "corpo_excessivo",
                            "Arquivo ou requisição excede o limite permitido.",
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(
                408, "tempo_esgotado", "Transferência incompleta. Selecione o arquivo novamente."
            )
            return
        sent = False

        async def buffered():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        started = False
        response_start = None
        response_body = bytearray()
        excessive = False
        response_limit = 2 * 1024 * 1024 if scope["path"].endswith("/relatorio") else 512 * 1024

        async def track(message):
            nonlocal started, response_start, excessive
            if message["type"] == "http.response.start":
                response_start = message
                if scope["path"].split("/")[1] in {"contas-envio", "campanhas"}:
                    message["headers"] = [
                        *message.get("headers", []),
                        (b"cache-control", b"no-store"),
                    ]
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if len(response_body) + len(chunk) > response_limit:
                    excessive = True
                if not excessive:
                    response_body.extend(chunk)
                if not message.get("more_body", False):
                    started = True
                    if excessive:
                        await reject(
                            413,
                            "resposta_excessiva",
                            "Resultado excessivo. Reduza a página ou refine a busca.",
                        )
                    else:
                        await send(response_start)
                        await send({"type": "http.response.body", "body": bytes(response_body)})

        try:
            await self.app(scope, buffered, track)
        except Exception:
            if started:
                raise
            await reject(500, "erro_interno", "Não foi possível concluir a operação.")
