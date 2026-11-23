"""Rotas privadas finas; nenhuma operação de provedor."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import authenticated_user, session_token
from app.consentimentos.schemas import Categoria
from app.templates.schemas import (
    Plataforma,
    PreviaEntrada,
    TemplateEdicao,
    TemplateEntrada,
    VersaoEntrada,
)

router = APIRouter(prefix="/templates", dependencies=[Depends(authenticated_user)])
Token = Annotated[str, Depends(session_token)]


def service(request: Request):
    return request.app.state.templates


@router.get("")
def listar(
    request: Request,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(20, ge=1, le=50),
    q: str = Query("", max_length=120),
    categoria: Categoria | None = None,
    plataforma: Plataforma | None = None,
    ativo: bool = True,
):
    return service(request).listar(pagina, limite, q, categoria, plataforma, ativo)


@router.post("", status_code=201)
def criar(request: Request, data: TemplateEntrada, token: Token):
    return service(request).criar(data, token)


@router.post("/previa")
def previa(request: Request, data: PreviaEntrada):
    return service(request).previa(data)


@router.get("/{template_id}")
def obter(request: Request, template_id: UUID):
    return service(request).obter(str(template_id))


@router.patch("/{template_id}")
def editar(request: Request, template_id: UUID, data: TemplateEdicao, token: Token):
    return service(request).editar(str(template_id), data, token)


@router.get("/{template_id}/versoes")
def historico(
    request: Request,
    template_id: UUID,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(20, ge=1, le=50),
    plataforma: Plataforma | None = None,
):
    return service(request).historico(str(template_id), pagina, limite, plataforma)


@router.post("/{template_id}/versoes", status_code=201)
def versionar(request: Request, template_id: UUID, data: VersaoEntrada, token: Token):
    return service(request).versionar(str(template_id), data, token)
