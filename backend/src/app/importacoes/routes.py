"""Upload de bytes e operações autenticadas de importação."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import authenticated_user, session_token
from app.auth.schemas import SessionView
from app.importacoes.schemas import ConfiguracaoImportacao, NovaImportacao

router = APIRouter(prefix="/importacoes", dependencies=[Depends(authenticated_user)])
User = Annotated[SessionView, Depends(authenticated_user)]


@router.post("/{identifier}/amostra")
def amostra(request: Request, identifier: UUID, data: ConfiguracaoImportacao, user: User):
    return request.app.state.importacoes.amostra(str(identifier), user.usuario.id, data)


@router.post("", status_code=201)
def criar(request: Request, data: NovaImportacao, user: User):
    return request.app.state.importacoes.criar(data.nome_arquivo, user.usuario.id)


@router.get("")
def listar(request: Request, pagina: int = Query(1, ge=1), limite: int = Query(50, ge=1, le=100)):
    return request.app.state.importacoes.listar(pagina, limite)


@router.get("/{identifier}")
def obter(request: Request, identifier: UUID):
    return request.app.state.importacoes.obter(str(identifier))


@router.put("/{identifier}/arquivo")
async def upload(request: Request, identifier: UUID, user: User):
    payload = await request.body()
    return await run_in_threadpool(
        request.app.state.importacoes.carregar, str(identifier), user.usuario.id, payload
    )


@router.post("/{identifier}/validacao", status_code=202)
def validar(request: Request, identifier: UUID, data: ConfiguracaoImportacao, user: User):
    return request.app.state.importacoes.validar(str(identifier), user.usuario.id, data)


@router.post("/{identifier}/confirmacao")
def confirmar(
    request: Request, identifier: UUID, user: User, token: Annotated[str, Depends(session_token)]
):
    return request.app.state.importacoes.confirmar(str(identifier), user.usuario.id, token)


@router.post("/{identifier}/cancelamento", status_code=204)
def cancelar(request: Request, identifier: UUID, user: User):
    request.app.state.importacoes.cancelar(str(identifier), user.usuario.id)
    return Response(status_code=204)


@router.get("/{identifier}/linhas")
def resultados(
    request: Request,
    identifier: UUID,
    pagina: int = Query(1, ge=1),
    limite: int = Query(50, ge=1, le=100),
    resultado: str | None = Query(None, max_length=30),
):
    return request.app.state.importacoes.resultados(str(identifier), pagina, limite, resultado)


@router.get("/{identifier}/relatorio")
def relatorio(request: Request, identifier: UUID):
    content = request.app.state.importacoes.relatorio(str(identifier))
    return Response(
        content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=erros-importacao.csv"},
    )
