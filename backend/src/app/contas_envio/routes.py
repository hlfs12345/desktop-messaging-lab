"""Rotas privadas de contas e verificação explícita Telegram."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import auth_service, session_token
from app.contas_envio.schemas import (
    ContaEdicao,
    ContaEntrada,
    CredencialEntrada,
    EstadoCadastro,
    Plataforma,
    Provedor,
    RevisaoEntrada,
)
from app.contas_envio.service import administrador

Token = Annotated[str, Depends(session_token)]


def admin(request: Request, token: Token):
    administrador(auth_service(request), token)


router = APIRouter(prefix="/contas-envio", dependencies=[Depends(admin)])


@router.get("")
def listar(
    request: Request,
    token: Token,
    pagina: int = Query(1, ge=1, le=1000000),
    limite: int = Query(20, ge=1, le=50),
    q: str = Query("", max_length=120),
    plataforma: Plataforma | None = None,
    provedor: Provedor | None = None,
    estado: EstadoCadastro = "ativa",
):
    return request.app.state.contas_envio.listar(
        token, pagina, limite, q, plataforma, provedor, estado
    )


@router.post("", status_code=201)
def criar(request: Request, data: ContaEntrada, token: Token):
    return request.app.state.contas_envio.criar(data, token)


@router.get("/{conta_id}")
def obter(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.obter(str(conta_id), token)


@router.patch("/{conta_id}")
def editar(request: Request, conta_id: UUID, data: ContaEdicao, token: Token):
    return request.app.state.contas_envio.editar(str(conta_id), data, token)


@router.post("/{conta_id}/verificar-telegram")
def verificar_telegram(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.verificar_telegram(str(conta_id), token)


@router.get("/{conta_id}/whatsapp")
def status_whatsapp(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.status_whatsapp(str(conta_id), token)


@router.post("/{conta_id}/whatsapp/parear")
def parear_whatsapp(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.conectar_whatsapp(
        str(conta_id), token, parear=True
    )


@router.post("/{conta_id}/whatsapp/reconectar")
def reconectar_whatsapp(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.conectar_whatsapp(
        str(conta_id), token, parear=False
    )


@router.post("/{conta_id}/whatsapp/desconectar")
def desconectar_whatsapp(request: Request, conta_id: UUID, token: Token):
    return request.app.state.contas_envio.desconectar_whatsapp(str(conta_id), token)


@router.post("/{conta_id}/whatsapp/desvincular")
def desvincular_whatsapp(
    request: Request, conta_id: UUID, data: RevisaoEntrada, token: Token
):
    return request.app.state.contas_envio.desvincular_whatsapp(
        str(conta_id), data, token
    )


@router.put("/{conta_id}/credencial")
def armazenar(request: Request, conta_id: UUID, data: CredencialEntrada, token: Token):
    return request.app.state.contas_envio.credencial(str(conta_id), data, token)


@router.delete("/{conta_id}/credencial")
def excluir(request: Request, conta_id: UUID, data: RevisaoEntrada, token: Token):
    return request.app.state.contas_envio.credencial(str(conta_id), data, token, excluir=True)
