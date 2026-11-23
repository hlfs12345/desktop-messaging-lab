"""Driver exclusivo de ensaio: comando interno explicito e checkpoints de processo.

Nunca incluido no produto. STDOUT continua reservado ao protocolo real.
"""

import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, select
from starlette.concurrency import run_in_threadpool

from app.auth.schemas import LoginInput
from app.campanhas.execucoes_models import ExecucaoCampanha
from app.campanhas.executor_mock import ExecutorMock
from app.provedores.mock import MockProvedor
from app.runtime import launcher

folder, checkpoint = Path(sys.argv[1]), sys.argv[2]
original_create = launcher.create_app
persistindo = False


def fase(nome):
    (folder / (nome + ".ready")).touch()
    if checkpoint == nome:
        deadline = time.monotonic() + 20
        while not (folder / "release").exists():
            if time.monotonic() >= deadline:
                os._exit(70)
            time.sleep(0.01)


def app_ensaio(**kwargs):
    application = original_create(**kwargs)
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            rt = app.state.execution_runtime
            rt.lifetime.subscribe(lambda: (folder / "barreira.ready").touch())
            deadline = time.monotonic() + 5
            while not rt.lifetime.is_open():
                if time.monotonic() > deadline:
                    raise RuntimeError("Evento integrado de teste indisponivel.")
                await asyncio.sleep(0.01)
            with rt.svc.repo.transacao() as s:
                e = s.scalar(select(ExecucaoCampanha))
                cid, eid = e.campanha_id, e.id
            result = await run_in_threadpool(
                app.state.auth.login,
                LoginInput(login="teste", senha="frase fictícia de teste 489!"),
            )
            original_reservar, original_marcar = ExecutorMock._reservar, ExecutorMock._marcar
            original_resultado, original_mock = ExecutorMock._resultado, MockProvedor.executar

            def reservar(self, *args):
                result = original_reservar(self, *args)
                fase("reserva")
                return result

            def marcar(self, *args):
                result = original_marcar(self, *args)
                fase("marcador")
                return result

            def mock(self, entrada):
                fase("mock")
                result = original_mock(self, entrada)
                fase("resposta")
                return result

            def resultado(self, *args):
                global persistindo
                persistindo = True
                original_resultado(self, *args)
                persistindo = False
                fase("resultado")

            def commit(_):
                if persistindo:
                    fase("commit_resultado")

            ExecutorMock._reservar, ExecutorMock._marcar = reservar, marcar
            ExecutorMock._resultado, MockProvedor.executar = resultado, mock
            event.listen(app.state.persistence.database.engine, "commit", commit)
            review = await run_in_threadpool(rt.review, cid, eid, result.token)
            await run_in_threadpool(rt.activate, review, result.token, confirmo=True)
            yield

    application.router.lifespan_context = lifespan
    return application


launcher.create_app = app_ensaio
raise SystemExit(launcher.run())
