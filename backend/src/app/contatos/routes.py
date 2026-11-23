"""Rotas do cadastro, protegidas pelas duas credenciais existentes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import authenticated_user
from app.auth.schemas import SessionView
from app.consentimentos.schemas import Categoria, Estado
from app.contatos.errors import NegocioError
from app.contatos.schemas import Arquivamento, ContatoEdicao, ContatoEntrada, TelefoneEntrada

router = APIRouter(prefix="/contatos", dependencies=[Depends(authenticated_user)])
User = Annotated[SessionView, Depends(authenticated_user)]


def service(request: Request):
    return request.app.state.contatos


@router.get("")
def listar(
    request: Request,
    q: str = Query("", max_length=120),
    ativo: bool | None = True,
    lista_id: UUID | None = None,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(50, ge=1, le=100),
    escopo_id: UUID | None = None,
    categoria: Categoria | None = None,
    consentimento: Estado | None = None,
    canal: Literal["whatsapp"] | None = None,
):
    if any((escopo_id, categoria, consentimento)) and not all(
        (escopo_id, categoria, consentimento)
    ):
        raise NegocioError("validacao", "Informe escopo, categoria e estado juntos.")
    return service(request).listar(
        q,
        ativo,
        str(lista_id) if lista_id else None,
        pagina,
        limite,
        str(escopo_id) if escopo_id else None,
        categoria,
        consentimento,
        canal,
    )


@router.post("", status_code=201)
def criar(request: Request, data: ContatoEntrada, user: User):
    return service(request).criar(data, user.usuario.id)


@router.get("/{identifier}")
def obter(request: Request, identifier: UUID):
    return service(request).obter(str(identifier))


@router.patch("/{identifier}")
def editar(request: Request, identifier: UUID, data: ContatoEdicao, user: User):
    return service(request).editar(str(identifier), data, user.usuario.id)


@router.post("/{identifier}/telefones", status_code=201)
def adicionar(request: Request, identifier: UUID, data: TelefoneEntrada, user: User):
    return service(request).adicionar_telefone(str(identifier), data, user.usuario.id)


@router.patch("/{identifier}/telefones/{phone_id}")
def arquivar(request: Request, identifier: UUID, phone_id: UUID, data: Arquivamento, user: User):
    return service(request).arquivar_telefone(str(identifier), str(phone_id), data, user.usuario.id)
