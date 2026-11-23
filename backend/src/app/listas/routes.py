"""Rotas de listas e participantes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.dependencies import authenticated_user
from app.auth.schemas import SessionView
from app.contatos.schemas import ListaEdicao, ListaEntrada

router = APIRouter(prefix="/listas", dependencies=[Depends(authenticated_user)])
User = Annotated[SessionView, Depends(authenticated_user)]


@router.get("")
def listar(
    request: Request,
    q: str = Query("", max_length=120),
    ativo: bool | None = True,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(50, ge=1, le=100),
):
    return request.app.state.listas.listar(q, ativo, pagina, limite)


@router.post("", status_code=201)
def criar(request: Request, data: ListaEntrada, user: User):
    return request.app.state.listas.criar(data, user.usuario.id)


@router.get("/{identifier}")
def obter(request: Request, identifier: UUID):
    return request.app.state.listas.obter(str(identifier))


@router.patch("/{identifier}")
def editar(request: Request, identifier: UUID, data: ListaEdicao, user: User):
    return request.app.state.listas.editar(str(identifier), data, user.usuario.id)


@router.get("/{identifier}/contatos")
def participantes(
    request: Request,
    identifier: UUID,
    pagina: int = Query(1, ge=1),
    limite: int = Query(50, ge=1, le=100),
):
    request.app.state.listas.obter(str(identifier))
    return request.app.state.contatos.listar(
        ativo=None, lista_id=str(identifier), pagina=pagina, limite=limite
    )


@router.put("/{identifier}/contatos/{contact_id}", status_code=204)
def associar(request: Request, identifier: UUID, contact_id: UUID, user: User):
    request.app.state.listas.associar(str(identifier), str(contact_id), user.usuario.id)
    return Response(status_code=204)


@router.delete("/{identifier}/contatos/{contact_id}", status_code=204)
def remover(request: Request, identifier: UUID, contact_id: UUID, user: User):
    request.app.state.listas.associar(
        str(identifier), str(contact_id), user.usuario.id, remover=True
    )
    return Response(status_code=204)
