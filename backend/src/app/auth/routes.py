"""Rotas finas; autorização da instância é aplicada antes pelo middleware."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.auth.dependencies import auth_service, authenticated_user, session_token
from app.auth.schemas import BootstrapInput, LoginInput, LoginResult, PublicUser, SessionView
from app.auth.service import AuthService

router = APIRouter(prefix="/auth")
Service = Annotated[AuthService, Depends(auth_service)]


@router.get("/configuracao-inicial")
def setup_state(service: Service) -> dict[str, bool]:
    return {"necessaria": service.setup_needed()}


@router.post("/configuracao-inicial", status_code=201, response_model=PublicUser)
def bootstrap(data: BootstrapInput, service: Service) -> PublicUser:
    return service.bootstrap(data)


@router.post("/login", response_model=LoginResult)
def login(data: LoginInput, service: Service) -> LoginResult:
    return service.login(data)


@router.get("/usuario", response_model=SessionView)
def current(user: Annotated[SessionView, Depends(authenticated_user)]) -> SessionView:
    return user


@router.post("/logout", status_code=204)
def logout(service: Service, token: Annotated[str, Depends(session_token)]) -> Response:
    # Idempotente inclusive para resposta perdida após commit, expiração ou
    # revogação anterior. O formato do token e a instância continuam exigidos.
    service.logout(token)
    return Response(status_code=204)
