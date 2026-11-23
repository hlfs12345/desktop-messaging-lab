"""API local com prontidão condicionada à persistência."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.auth.errors import AuthError
from app.auth.http_boundary import AuthBoundaryMiddleware, auth_error, validation_error
from app.auth.repository import AuthRepository
from app.auth.routes import router as auth_router
from app.auth.service import AuthService
from app.campanhas.autorizacoes import AutorizacoesSimulacaoService
from app.campanhas.envios_telegram import EnviosTelegramService
from app.campanhas.envios_whatsapp import EnviosWhatsappService
from app.campanhas.execucoes import ExecucoesService
from app.campanhas.execucoes_http import ExecucoesHTTPService
from app.campanhas.execucoes_routes import router as execucoes_router
from app.campanhas.preparacoes import PreparacoesService
from app.campanhas.routes import router as campanhas_router
from app.campanhas.service import CampanhasService
from app.consentimentos.routes import router as consentimentos_router
from app.consentimentos.service import ConsentimentosService
from app.contas_envio.routes import router as contas_router
from app.contas_envio.segredos import SegredosWindows
from app.contas_envio.service import ContasService
from app.contatos.errors import NegocioError
from app.contatos.http_boundary import NegocioBoundaryMiddleware, negocio_error
from app.contatos.repository import CadastroRepository
from app.contatos.routes import router as contatos_router
from app.contatos.service import ContatosService
from app.core.config import Settings
from app.core.instance_auth import InstanceAuthMiddleware, InstanceAuthorization
from app.importacoes.routes import router as importacoes_router
from app.importacoes.service import ImportacoesService
from app.listas.routes import router as listas_router
from app.listas.service import ListasService
from app.persistencia.errors import PersistenceError
from app.persistencia.lifecycle import Persistence
from app.provedores.whatsapp_baileys import WhatsappBaileys
from app.runtime.execution_lifecycle import ExecutionLifecycle
from app.runtime.telegram_recepcao import RecepcaoTelegram
from app.templates.routes import router as templates_router
from app.templates.service import TemplatesService


def create_app(
    settings: Settings | None = None,
    *,
    authorization: InstanceAuthorization | None = None,
    preparing: Callable[[], None] | None = None,
    lifetime=None,
) -> FastAPI:
    """Cria uma instância independente da aplicação para execução e testes."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if authorization is None:
            raise RuntimeError("A API exige contexto privado de instância.")
        try:
            if preparing:
                preparing()
            configuration = settings or await run_in_threadpool(Settings.from_environment)
            persistence = Persistence(configuration)
            await run_in_threadpool(persistence.start)
            try:
                service = await run_in_threadpool(AuthService, AuthRepository(persistence.database))
                await run_in_threadpool(service.initialize)
            except Exception:
                await run_in_threadpool(persistence.close)
                raise PersistenceError("Não foi possível preparar a autenticação local.") from None
        except PersistenceError as error:
            application.state.startup_error = type(error)
            logging.getLogger("uvicorn.error").error("Persistência indisponível: %s", error)
            raise RuntimeError(str(error)) from None
        application.state.persistence = persistence
        application.state.auth = service
        repository = CadastroRepository(persistence.database)
        application.state.contatos = ContatosService(repository)
        application.state.consentimentos = ConsentimentosService(repository, service)
        application.state.templates = TemplatesService(repository, service)
        application.state.campanhas = CampanhasService(
            repository, service, application.state.consentimentos, application.state.templates
        )
        whatsapp = WhatsappBaileys()
        application.state.whatsapp = whatsapp
        application.state.contas_envio = ContasService(
            repository, service, SegredosWindows(configuration), whatsapp=whatsapp
        )
        application.state.preparacoes = PreparacoesService(application.state.campanhas)
        application.state.autorizacoes_simulacao = AutorizacoesSimulacaoService(
            application.state.preparacoes
        )
        application.state.envios_telegram = EnviosTelegramService(
            application.state.preparacoes, application.state.contas_envio
        )
        application.state.envios_whatsapp = EnviosWhatsappService(
            application.state.preparacoes, whatsapp
        )
        application.state.listas = ListasService(repository)
        imports = None
        execution = None
        telegram = None
        try:
            application.state.execucoes = ExecucoesService(
                application.state.autorizacoes_simulacao,
                application.state.envios_telegram,
            )
            telegram = RecepcaoTelegram(
                application.state.consentimentos, application.state.contas_envio, lifetime
            )
            application.state.telegram_recepcao = telegram
            application.state.envios_telegram.recepcao = telegram
            execution = ExecutionLifecycle(application.state.execucoes, persistence, lifetime)
            application.state.execution_runtime = execution
            application.state.execucoes_http = ExecucoesHTTPService(
                application.state.execucoes, execution
            )

            def session_changed(token=None):
                application.state.envios_telegram.parar()
                execution.session_changed(token)
                telegram.parar(token)
                whatsapp.suspender_todos()

            service.session_changed = session_changed
            service.session_started = telegram.iniciar
            await run_in_threadpool(execution.start)
            await run_in_threadpool(application.state.envios_telegram.recuperar)
            await run_in_threadpool(application.state.envios_whatsapp.recuperar)
            await run_in_threadpool(application.state.contas_envio.recuperar)
            imports = await run_in_threadpool(ImportacoesService, repository, service)
            application.state.importacoes = imports
            yield
        finally:
            if lifetime is not None:
                lifetime.close()
            await run_in_threadpool(whatsapp.close)
            if not await run_in_threadpool(application.state.envios_telegram.drenar):
                application.state.shutdown_unconfirmed = True
                raise RuntimeError("Envio Telegram nao encerrou; tentativa preservada.")
            if execution is not None and not await run_in_threadpool(execution.drain):
                application.state.shutdown_unconfirmed = True
                raise RuntimeError(
                    "Executor nao encerrou no prazo; persistencia preservada para contencao."
                )
            if telegram is not None and not await run_in_threadpool(telegram.drenar):
                application.state.shutdown_unconfirmed = True
                raise RuntimeError("Recepção Telegram não encerrou; persistência preservada.")
            try:
                if imports is not None:
                    try:
                        await run_in_threadpool(imports.close, espera=0.25)
                        application.state.shutdown_imports_reconciled = True
                    except NegocioError as error:
                        if error.codigo != "banco_ocupado":
                            raise
                        application.state.shutdown_imports_reconciled = False
                        logging.getLogger("uvicorn.error").warning(
                            "Recuperação de importações pendente para a próxima abertura."
                        )
            finally:
                try:
                    try:
                        await run_in_threadpool(service.initialize, busy_timeout_ms=250)
                        application.state.shutdown_sessions_revoked = True
                    except AuthError:
                        # A API será fechada. No próximo startup a revogação é obrigatória
                        # antes de READY; falhar aqui não autoriza reutilizar sessões.
                        application.state.shutdown_sessions_revoked = False
                        logging.getLogger("uvicorn.error").warning(
                            "Revogação de sessões ao fechar indisponível; "
                            "reabertura exige revogação."
                        )
                finally:
                    service.close()
                    await run_in_threadpool(persistence.close)
                    application.state.shutdown_complete = True

    application = FastAPI(
        title="Messaging Lab",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.add_exception_handler(AuthError, auth_error)
    application.add_exception_handler(NegocioError, negocio_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.include_router(auth_router)
    application.include_router(templates_router)
    application.include_router(campanhas_router)
    application.include_router(execucoes_router)
    application.include_router(contas_router)
    application.include_router(consentimentos_router)
    application.include_router(contatos_router)
    application.include_router(listas_router)
    application.include_router(importacoes_router)
    application.add_middleware(AuthBoundaryMiddleware)
    application.add_middleware(NegocioBoundaryMiddleware)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    application.add_middleware(InstanceAuthMiddleware, authorization=authorization)

    @application.get("/health")
    def health() -> JSONResponse:
        """Confere disponibilidade e revisão esperada, fora do event loop."""
        persistence = getattr(application.state, "persistence", None)
        if persistence is None or not persistence.probe():
            return JSONResponse({"status": "indisponivel"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return application


app = create_app()
