"""Dependências reutilizáveis pelas futuras rotas de negócio."""

from typing import Annotated

from fastapi import Depends, Request

from app.auth.errors import invalid_session, unavailable
from app.auth.schemas import SessionView
from app.auth.service import AuthService, token_digest


def auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise unavailable()
    return service


def session_token(request: Request) -> str:
    values = request.headers.getlist("x-edisk-session")
    if len(values) != 1:
        raise invalid_session()
    token_digest(values[0])
    return values[0]


def authenticated_user(
    service: Annotated[AuthService, Depends(auth_service)],
    token: Annotated[str, Depends(session_token)],
) -> SessionView:
    return service.current(token)
