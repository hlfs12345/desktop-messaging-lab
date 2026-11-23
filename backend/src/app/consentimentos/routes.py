"""Rotas finas; a fronteira /contatos aplica as duas credenciais existentes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import authenticated_user, session_token
from app.consentimentos.schemas import (
    Categoria,
    EscopoEntrada,
    EventoEntrada,
    IdentidadeEntrada,
    ListaAutorizacaoEntrada,
)

router = APIRouter(prefix="/contatos", dependencies=[Depends(authenticated_user)])
Token = Annotated[str, Depends(session_token)]


def service(request: Request):
    return request.app.state.consentimentos


@router.get("/escopos-comunicacao")
def escopos(request: Request):
    return service(request).escopos()


@router.post("/escopos-comunicacao", status_code=201)
def criar_escopo(request: Request, data: EscopoEntrada, token: Token):
    return service(request).criar_escopo(data, token)


@router.post("/listas/{lista_id}/autorizacoes-whatsapp", status_code=201)
def autorizar_lista(
    request: Request, lista_id: UUID, data: ListaAutorizacaoEntrada, token: Token
):
    return service(request).autorizar_lista(str(lista_id), data, token)


@router.get("/{contato_id}/identidades")
def identidades(request: Request, contato_id: UUID):
    return service(request).identidades(str(contato_id))


@router.post("/{contato_id}/identidades", status_code=201)
def criar_identidade(request: Request, contato_id: UUID, data: IdentidadeEntrada, token: Token):
    return service(request).criar_identidade(str(contato_id), data, token)


@router.get("/{contato_id}/consentimentos")
def estado(request: Request, contato_id: UUID, escopo_id: UUID):
    return service(request).estado(str(contato_id), str(escopo_id))


@router.post("/{contato_id}/consentimentos/eventos", status_code=201)
def registrar(request: Request, contato_id: UUID, data: EventoEntrada, token: Token):
    return service(request).registrar(str(contato_id), data, token)


@router.get("/{contato_id}/consentimentos/eventos")
def historico(
    request: Request,
    contato_id: UUID,
    escopo_id: UUID,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(20, ge=1, le=50),
):
    return service(request).historico(str(contato_id), str(escopo_id), pagina, limite)


@router.get("/{contato_id}/elegibilidade-consentimento")
def elegibilidade(
    request: Request,
    contato_id: UUID,
    escopo_id: UUID,
    identidade_id: UUID,
    categoria: Categoria,
):
    return service(request).elegibilidade(
        str(contato_id),
        str(escopo_id),
        str(identidade_id),
        categoria,
    )
