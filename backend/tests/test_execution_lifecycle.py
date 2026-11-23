"""Marco C em processo: eventos controlados; ensaios Windows separados usam Job real."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
import test_executor_mock as b2
import test_executor_mock_desempenho as volume
from fastapi.testclient import TestClient
from helpers.runtime_lifetime import SimulatedLifetime
from sqlalchemy.exc import OperationalError

from app.auth.errors import AuthError
from app.auth.schemas import LoginInput
from app.campanhas.executor_mock import ContextoOperacionalMock, ExecutorMock
from app.contatos.errors import NegocioError
from app.main import create_app
from app.runtime.execution_lifecycle import ExecutionLifecycle

cadastro = b2.cadastro
consentimento = b2.consentimento
rascunho = b2.rascunho
pronta = b2.pronta
simulavel = b2.simulavel


@pytest.fixture
def integrada(simulavel):
    volume.popular(simulavel, 3)
    f, receipt, ctx, _, _ = b2.construir(simulavel)
    ctx.fechar()  # Nenhum passo; so o controller do C pode ativar o worker integrado.
    runtime = f["app"].state.execution_runtime
    lifetime = SimulatedLifetime()
    runtime.lifetime = lifetime
    lifetime.subscribe(runtime.close_admission)
    yield f, receipt, runtime, lifetime
    lifetime.close()


def revisao(f, receipt, runtime):
    return runtime.review(f["cid"], receipt["execucao_id"], f["token"])


def esperar(runtime):
    with runtime._condition:
        assert runtime._condition.wait_for(
            lambda: (
                runtime._active is None and not runtime._reconciling and not runtime._activating
            ),
            timeout=10,
        )


def test_worker_desarmado_revisao_nao_ativa_e_inicio_explicito(integrada):
    f, receipt, rt, _ = integrada
    assert rt._active is None and b2.linhas(f, "tentativas_envio") == []
    review = revisao(f, receipt, rt)
    assert review["permitida"] and b2.linhas(f, "tentativas_envio") == []
    with pytest.raises(NegocioError):
        rt.activate(review, f["token"])
    rt.activate(review, f["token"], confirmo=True)
    esperar(rt)
    assert len(b2.linhas(f, "tentativas_envio")) == 3
    assert b2.estado(f, receipt) == "concluida"
    assert rt._context is None


def test_fechamento_antes_do_startup_nao_vira_erro(settings, authorization):
    lifetime = SimulatedLifetime()
    lifetime.close()
    app = create_app(settings, authorization=authorization, lifetime=lifetime)
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {authorization.token}"}
    ) as client:
        assert client.get("/health").status_code == 200
        assert app.state.execution_runtime._thread is None
    assert app.state.shutdown_complete and app.state.persistence.database.closed


def test_falha_recuperacao_no_lifespan_preserva_consulta(settings, authorization, monkeypatch):
    def falhar(_):
        raise NegocioError("recuperacao", "Indisponivel", 503)

    monkeypatch.setattr(ExecutionLifecycle, "_recover", falhar)
    app = create_app(settings, authorization=authorization, lifetime=SimulatedLifetime())
    with TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {authorization.token}"}
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/auth/configuracao-inicial").json() == {"necessaria": True}
        assert not app.state.execution_runtime.recovery_ok
        with pytest.raises(NegocioError):
            app.state.execution_runtime.review("ausente", "ausente", "ausente")


@pytest.mark.parametrize("alteracao", ["versao", "marcador", "usuario_id", "sessao_id", "abertura"])
def test_revisao_adulterada_nao_ativa(integrada, alteracao):
    f, receipt, rt, _ = integrada
    review = revisao(f, receipt, rt)
    review[alteracao] = review[alteracao] + 1 if alteracao == "versao" else "adulterado"
    with pytest.raises(NegocioError):
        rt.activate(review, f["token"], confirmo=True)
    assert b2.linhas(f, "tentativas_envio") == []


def test_ausencia_launcher_e_fechamento_negam_ativacao(integrada):
    f, receipt, rt, lifetime = integrada
    rt.lifetime = None
    with pytest.raises(NegocioError):
        revisao(f, receipt, rt)
    rt.lifetime = lifetime
    review = revisao(f, receipt, rt)
    lifetime.close()
    with pytest.raises(NegocioError):
        rt.activate(review, f["token"], confirmo=True)
    assert b2.linhas(f, "tentativas_envio") == []


def test_nem_admissao_primitiva_dispensa_ciclo_de_vida(integrada):
    f, receipt, rt, _ = integrada
    with pytest.raises(NegocioError):
        rt.start()
    with pytest.raises(NegocioError):
        ExecutionLifecycle(rt.svc, rt.persistence, rt.lifetime)
    ctx = ContextoOperacionalMock(f["svc"], rt.persistence, f["svc"].p.abertura)
    try:
        with pytest.raises(NegocioError):
            ExecutorMock(f["svc"], ctx).admitir(receipt["execucao_id"], f["token"], 1)
    finally:
        ctx.fechar()
    assert b2.linhas(f, "tentativas_envio") == []


def test_troca_sessao_exige_revisao_operacional_nova(integrada):
    f, receipt, rt, _ = integrada
    antiga = revisao(f, receipt, rt)
    token = (
        f["app"]
        .state.auth.login(LoginInput(login="teste", senha="frase fictícia de teste 489!"))
        .token
    )
    with pytest.raises(AuthError):
        rt.activate(antiga, f["token"], confirmo=True)
    with pytest.raises(NegocioError):
        rt.activate(antiga, token, confirmo=True)
    f["token"] = token
    rt.activate(revisao(f, receipt, rt), token, confirmo=True)
    esperar(rt)
    assert len(b2.linhas(f, "tentativas_envio")) == 3


@pytest.mark.parametrize("fase", ["reserva", "marcador", "mock", "persistencia", "resultado"])
def test_fechamento_drena_sem_novo_despacho(integrada, monkeypatch, fase):
    f, receipt, rt, lifetime = integrada
    entrou, liberar = Event(), Event()
    method = {
        "reserva": "_reservar",
        "marcador": "_marcar",
        "mock": "_invocar",
        "persistencia": "_resultado",
        "resultado": "_resultado",
    }[fase]
    original = getattr(ExecutorMock, method)

    def bloquear(self, *args, **kwargs):
        antes = fase in {"mock", "persistencia"}

        def executar():
            return original(*args, **kwargs) if fase == "mock" else original(self, *args, **kwargs)

        if not antes:
            result = executar()
        entrou.set()
        assert liberar.wait(10)
        return executar() if antes else result

    monkeypatch.setattr(ExecutorMock, method, bloquear)
    try:
        rt.activate(revisao(f, receipt, rt), f["token"], confirmo=True)
        assert entrou.wait(10)
        ctx = rt._context
        lifetime.close()
        assert ctx.fechado.is_set() and not ctx._selos
        assert not f["db"].closed and not f["app"].state.auth._closed.is_set()
    finally:
        liberar.set()
    assert rt.drain()
    rows = b2.linhas(f, "tentativas_envio")
    assert len(rows) == 1 and rows[0]["estado"] == {
        "reserva": "interrompida",
        "marcador": "desconhecida",
    }.get(fase, "aceita_provedor")
    assert b2.estado(f, receipt) == "pausada"


def test_drenagem_tem_prazo_e_nao_fecha_persistencia(integrada, monkeypatch):
    f, receipt, rt, _ = integrada
    entrou, liberar = Event(), Event()
    original = ExecutorMock._invocar

    def bloquear(self, *args):
        entrou.set()
        assert liberar.wait(10)
        return original(*args)

    monkeypatch.setattr(ExecutorMock, "_invocar", bloquear)
    try:
        rt.activate(revisao(f, receipt, rt), f["token"], confirmo=True)
        assert entrou.wait(10)
        assert not rt.drain(timeout=0.01)
        assert not f["db"].closed
    finally:
        liberar.set()
    assert rt.drain()


def test_duas_ativacoes_somente_uma_admitida(integrada, monkeypatch):
    f, receipt, rt, _ = integrada
    review = revisao(f, receipt, rt)
    barreira, entrou, liberar = Barrier(2), Event(), Event()
    original = ExecutorMock._invocar

    def bloquear(self, *args):
        entrou.set()
        assert liberar.wait(10)
        return original(*args)

    monkeypatch.setattr(ExecutorMock, "_invocar", bloquear)

    def ativar(_):
        barreira.wait(timeout=10)
        try:
            return rt.activate(review, f["token"], confirmo=True)
        except NegocioError:
            return None

    with ThreadPoolExecutor(2) as pool:
        try:
            results = list(pool.map(ativar, [0, 1]))
            assert sum(r is not None for r in results) == 1
            assert entrou.wait(10)
            rt.close_admission()
        finally:
            liberar.set()
    assert rt.drain() and len(b2.linhas(f, "tentativas_envio")) == 1


@pytest.mark.parametrize("mudanca", ["logout", "troca_sessao", "revogacao", "optout"])
def test_mutacao_concorrente_preserva_resultado_admitido(integrada, monkeypatch, mudanca):
    f, receipt, rt, _ = integrada
    entrou, liberar = Event(), Event()
    original = ExecutorMock._invocar

    def bloquear(self, *args):
        entrou.set()
        assert liberar.wait(10)
        return original(*args)

    monkeypatch.setattr(ExecutorMock, "_invocar", bloquear)
    rt.activate(revisao(f, receipt, rt), f["token"], confirmo=True)
    assert entrou.wait(10)
    ctx = rt._context
    with ThreadPoolExecutor(1) as pool:
        try:
            if mudanca == "logout":
                f["app"].state.auth.logout(f["token"])
                assert not ctx._selos
            elif mudanca == "troca_sessao":
                f["app"].state.auth.login(
                    LoginInput(login="teste", senha="frase fictícia de teste 489!")
                )
                assert not ctx._selos
            elif mudanca == "revogacao":
                pending = pool.submit(b2.b1.revogar, f)
            else:
                # Bloqueio global fecha o escopo inteiro; sinaliza antes do lock.
                pending = pool.submit(b2.b1.anterior.bloquear, f, "bloqueio_global")
            liberar.set()
            if mudanca in {"revogacao", "optout"}:
                pending.result(timeout=10)
        finally:
            liberar.set()
    esperar(rt)
    rows = b2.linhas(f, "tentativas_envio")
    assert rows[0]["estado"] == "aceita_provedor"
    assert all(r["estado"] != "iniciada" for r in rows)


def test_startup_recupera_sem_ativar_e_retomada_pede_nova_revisao(integrada, monkeypatch):
    f, receipt, rt, _ = integrada
    # Reserva/marcador abandonados por executor antigo, sem chamar o mock.
    ctx, executor = b2.integrar(f)
    adm = b2.admitir(f, executor, receipt)
    tid = executor._reservar(adm)
    executor._marcar(adm, tid)
    ctx.fechar()
    rt._recover()
    antes = b2.b1.anterior.snapshot(f)
    rt._recover()
    assert b2.b1.anterior.snapshot(f) == antes
    assert b2.estado(f, receipt) == "pausada" and rt._active is None
    with pytest.raises(NegocioError):
        executor._resultado(adm, tid, "aceita_provedor", "aceita")
    review = revisao(f, receipt, rt)
    assert review["estado"] == "pausada"
    rt.activate(review, f["token"], confirmo=True)
    esperar(rt)
    rows = b2.linhas(f, "tentativas_envio")
    assert len(rows) == 3 and sum(r["estado"] == "desconhecida" for r in rows) == 1


def test_falha_recuperacao_bloqueia_processamento_preserva_health(integrada, monkeypatch):
    f, receipt, rt, _ = integrada

    def falhar(*_, **__):
        raise OperationalError("falha", None, None)

    monkeypatch.setattr(f["svc"].repo, "auditar", falhar)
    try:
        rt._recover()
    except NegocioError:
        rt.recovery_ok = False
    assert not rt.recovery_ok
    with pytest.raises(NegocioError):
        revisao(f, receipt, rt)
    assert f["c"].get("/health").status_code == 200
