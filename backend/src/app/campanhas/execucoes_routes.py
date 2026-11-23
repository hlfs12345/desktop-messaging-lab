"""Rotas privadas finas de execucao mock; recuperacao continua no startup."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import authenticated_user, session_token
from app.campanhas.execucoes_schemas import (
    ComandoExecucaoSaida,
    ExecucaoSaida,
    IniciarExecucaoEntrada,
    ItemExecucaoSaida,
    PaginaExecucaoSaida,
    PausarExecucaoEntrada,
    RetomarExecucaoEntrada,
    TentativaSaida,
)

router = APIRouter(prefix="/campanhas", dependencies=[Depends(authenticated_user)])
Token = Annotated[str, Depends(session_token)]
Pagina = Annotated[int, Query(ge=1, le=1000000)]
Limite = Annotated[int, Query(ge=1, le=50)]


@router.get("/{cid}/preparacoes/{pid}/revisao-inicio")
def revisao_inicio(request: Request, cid: UUID, pid: UUID, token: Token):
    return request.app.state.execucoes_http.revisao_inicio(str(cid), str(pid), token)


@router.post(
    "/{cid}/preparacoes/{pid}/execucoes", status_code=201, response_model=ComandoExecucaoSaida
)
def iniciar(request: Request, cid: UUID, pid: UUID, data: IniciarExecucaoEntrada, token: Token):
    return request.app.state.execucoes_http.iniciar(str(cid), str(pid), data, token)


@router.get("/{cid}/execucoes", response_model=PaginaExecucaoSaida[ExecucaoSaida])
def listar(request: Request, cid: UUID, token: Token, pagina: Pagina = 1, limite: Limite = 20):
    return request.app.state.execucoes_http.listar(str(cid), token, pagina, limite)


@router.get("/{cid}/execucoes/{eid}", response_model=ExecucaoSaida)
def obter(request: Request, cid: UUID, eid: UUID, token: Token):
    return request.app.state.execucoes_http.obter(str(cid), str(eid), token)


@router.get("/{cid}/execucoes/{eid}/itens", response_model=PaginaExecucaoSaida[ItemExecucaoSaida])
def itens(
    request: Request, cid: UUID, eid: UUID, token: Token, pagina: Pagina = 1, limite: Limite = 20
):
    return request.app.state.execucoes_http.listar(
        str(cid), token, pagina, limite, eid=str(eid), tipo="itens"
    )


@router.get(
    "/{cid}/execucoes/{eid}/itens/{iid}/tentativas",
    response_model=PaginaExecucaoSaida[TentativaSaida],
)
def tentativas(
    request: Request,
    cid: UUID,
    eid: UUID,
    iid: UUID,
    token: Token,
    pagina: Pagina = 1,
    limite: Limite = 20,
):
    return request.app.state.execucoes_http.listar(
        str(cid), token, pagina, limite, eid=str(eid), iid=str(iid), tipo="tentativas"
    )


@router.post("/{cid}/execucoes/{eid}/pausa", response_model=ComandoExecucaoSaida)
def pausar(request: Request, cid: UUID, eid: UUID, data: PausarExecucaoEntrada, token: Token):
    return request.app.state.execucoes_http.pausar(str(cid), str(eid), data, token)


@router.get("/{cid}/execucoes/{eid}/revisao-retomada")
def revisao_retomada(request: Request, cid: UUID, eid: UUID, token: Token):
    return request.app.state.execucoes_http.revisao_retomada(str(cid), str(eid), token)


@router.post("/{cid}/execucoes/{eid}/retomada", response_model=ComandoExecucaoSaida)
def retomar(request: Request, cid: UUID, eid: UUID, data: RetomarExecucaoEntrada, token: Token):
    return request.app.state.execucoes_http.retomar(str(cid), str(eid), data, token)
