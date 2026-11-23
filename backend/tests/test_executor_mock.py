"""B2 com SQLite real descartavel; falhas controladas nao simulam morte do processo."""

import json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event, get_ident
from uuid import uuid4

import pytest
import test_execucoes_service as b1
from helpers.runtime_lifetime import SimulatedLifetime
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.campanhas.executor_mock import ContextoOperacionalMock, ExecutorMock
from app.contas_envio.schemas import ContaEdicao
from app.contatos.errors import NegocioError
from app.provedores.mock import MockProvedor

cadastro = b1.cadastro
consentimento = b1.consentimento
rascunho = b1.rascunho
pronta = b1.pronta
simulavel = b1.simulavel
confirmada = b1.confirmada
autorizada = b1.autorizada


def integrar(f):
    ctx = ContextoOperacionalMock(
        f["svc"], f["app"].state.persistence, f["svc"].p.abertura, lifetime=SimulatedLifetime()
    )
    executor = ExecutorMock(f["svc"], ctx)
    return ctx, executor


def admitir(f, executor, recibo, *, retomar=False):
    return executor.admitir(
        recibo["execucao_id"], f["token"], recibo["versao_execucao"], retomar=retomar
    )


@pytest.fixture
def operacional(autorizada):
    f = autorizada
    receipt = b1.iniciar(f)
    ctx, executor = integrar(f)
    adm = admitir(f, executor, receipt)
    return f, receipt, ctx, executor, adm


def construir(simulavel, cenario="aceita_provedor"):
    sim = b1.anterior.sim
    receipt, _ = sim.preparar(simulavel, sim.entrada(simulavel, cenario))
    sim.confirmar(simulavel, receipt)
    r, cid, _ = simulavel
    f = {
        "c": r[0],
        "app": r[1],
        "r": r,
        "simulavel": simulavel,
        "root": "/campanhas/" + cid,
        "pid": receipt["preparacao_id"],
    }
    auth, _ = b1.anterior.autorizar(f)
    f = {**b1.contexto(f), "autorizacao": auth}
    receipt = b1.iniciar(f)
    ctx, executor = integrar(f)
    return f, receipt, ctx, executor, admitir(f, executor, receipt)


def linhas(f, tabela):
    with f["db"].engine.begin() as conn:
        return [dict(r) for r in conn.exec_driver_sql("SELECT * FROM " + tabela).mappings()]


def estado(f, receipt):
    return b1.consulta(f, receipt)["execucao"]["estado"]


@pytest.mark.parametrize(
    "cenario,esperado",
    [
        ("aceita_provedor", "aceita_provedor"),
        ("falhou", "falhou"),
        ("desconhecida", "desconhecida"),
        ("interrupcao", "desconhecida"),
        ("excecao", "desconhecida"),
    ],
)
def test_cinco_cenarios_sem_transacao_durante_invocacao(simulavel, monkeypatch, cenario, esperado):
    f, receipt, _, executor, adm = construir(simulavel, cenario)
    original = MockProvedor.executar
    chamadas, transacoes = [], []

    def begin(_):
        transacoes.append(True)

    def end(_):
        transacoes.pop()

    def executar(self, entrada):
        assert not transacoes
        rows = linhas(f, "tentativas_envio")
        assert len(rows) == 1 and rows[0]["estado"] == "iniciada"
        assert rows[0]["invocacao_em"] is not None
        chamadas.append(entrada.tentativa_id)
        return original(self, entrada)

    engine = f["db"].engine
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", end)
    event.listen(engine, "rollback", end)
    monkeypatch.setattr(MockProvedor, "executar", executar)
    antes = b1.anterior.snapshot(f)
    try:
        result = executor.passo(adm)
    finally:
        event.remove(engine, "begin", begin)
        event.remove(engine, "commit", end)
        event.remove(engine, "rollback", end)
    assert len(chamadas) == 1 and result["estado"] == esperado
    assert estado(f, receipt) == (
        "concluida" if esperado == "aceita_provedor" else "concluida_com_falhas"
    )
    assert linhas(f, "tentativas_envio")[0]["estado"] == esperado
    assert linhas(f, "campanha_execucao_itens")[0]["estado"] == esperado
    depois = b1.anterior.snapshot(f)
    for tabela in (
        "campanha_preparacoes",
        "campanha_preparacao_itens",
        "campanha_autorizacoes_simulacao",
    ):
        assert antes[tabela] == depois[tabela]
    assert executor.recuperar()["reconciliadas"] == 0
    assert b1.anterior.snapshot(f) == depois


def test_admissao_explicita_e_contexto_real_obrigatorios(autorizada):
    f = autorizada
    receipt = b1.iniciar(f)
    antes = b1.anterior.snapshot(f)
    with pytest.raises(NegocioError):
        ExecutorMock(f["svc"]).passo(None)
    with pytest.raises(NegocioError):
        ContextoOperacionalMock(f["svc"], f["app"].state.persistence, str(uuid4()))
    ctx, executor = integrar(f)
    with pytest.raises(NegocioError):
        executor.passo(None)
    assert b1.anterior.snapshot(f) == antes
    ctx.fechar()
    with pytest.raises(NegocioError):
        admitir(f, executor, receipt)


@pytest.mark.parametrize("campo", ["proprietario_id", "geracao", "sessao_id", "abertura"])
def test_admissao_nao_pode_ser_fabricada(operacional, campo):
    f, _, _, executor, adm = operacional
    adulterada = replace(adm, **{campo: 99 if campo == "geracao" else str(uuid4())})
    antes = b1.anterior.snapshot(f)
    with pytest.raises(NegocioError):
        executor.passo(adulterada)
    assert b1.anterior.snapshot(f) == antes


def test_duplo_executor_e_reserva_concorrente(operacional, monkeypatch):
    f, _, ctx, executor, adm = operacional
    reservado, liberar = Event(), Event()
    original = executor._reservar

    def esperar(a):
        result = original(a)
        reservado.set()
        assert liberar.wait(10)
        return result

    monkeypatch.setattr(executor, "_reservar", esperar)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(executor.passo, adm)
        try:
            assert reservado.wait(10)
            with pytest.raises(NegocioError):
                ExecutorMock(f["svc"], ctx).passo(adm)
            with pytest.raises(NegocioError):
                integrar(f)
        finally:
            liberar.set()
        assert pending.result(timeout=20)["estado"] == "aceita_provedor"
    assert len(linhas(f, "tentativas_envio")) == 1


@pytest.mark.parametrize(
    "mutacao",
    [
        "expiracao",
        "logout",
        "revogacao",
        "optout",
        "conta",
        "chave",
        "preparacao",
        "contato",
        "telefone",
        "lista",
    ],
)
def test_revalidacao_apos_reserva_nao_invoca(operacional, monkeypatch, mutacao):
    f, receipt, ctx, executor, adm = operacional
    original = executor._reservar

    def mutar(a):
        tid = original(a)
        if mutacao == "expiracao":
            clock = f["app"].state.auth.clock
            monkeypatch.setattr(f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9))
        elif mutacao == "logout":
            f["app"].state.auth.logout(f["token"])
        elif mutacao == "revogacao":
            b1.revogar(f)
        elif mutacao == "optout":
            b1.anterior.bloquear(f, "revogou")
        elif mutacao == "conta":
            conta = f["c"].get(f["root"]).json()["conta_envio_id"]
            f["app"].state.contas_envio.editar(
                conta, ContaEdicao(operacao_id=uuid4(), versao=1, ativo=False), f["token"]
            )
        elif mutacao == "chave":
            monkeypatch.setattr(
                executor.p,
                "_ctx",
                lambda *_: (_ for _ in ()).throw(NegocioError("chave", "Indisponivel", 503)),
            )
        elif mutacao == "preparacao":
            monkeypatch.setattr(
                executor.p,
                "_obter",
                lambda *_: (_ for _ in ()).throw(NegocioError("preparacao", "Indisponivel", 409)),
            )
        elif mutacao == "contato":
            cid = f["r"][3]["id"]
            versao = f["c"].get("/contatos/" + cid).json()["versao"]
            response = f["c"].patch("/contatos/" + cid, json={"versao": versao, "ativo": False})
            assert response.status_code == 200
        elif mutacao == "telefone":
            cid, pid = f["r"][3]["id"], f["r"][4]["telefone_id"]
            response = f["c"].patch(
                "/contatos/" + cid + "/telefones/" + pid, json={"versao": 1, "ativo": False}
            )
            assert response.status_code == 200
        else:
            cid, lid = f["r"][3]["id"], f["r"][5]["id"]
            response = f["c"].delete("/listas/" + lid + "/contatos/" + cid)
            assert response.status_code == 204
        return tid

    monkeypatch.setattr(executor, "_reservar", mutar)
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Nao deve invocar"))
    if mutacao in {"expiracao", "logout", "chave", "preparacao"}:
        with pytest.raises(NegocioError):
            executor.passo(adm)
        assert ctx.suspenso.is_set()
        if ctx.fechado.is_set():
            _, novo = integrar(f)
            assert novo.recuperar()["reconciliadas"] == 1
        else:
            assert executor.recuperar()["reconciliadas"] == 1
    else:
        assert not executor.passo(adm)["processado"]
    rows = linhas(f, "tentativas_envio")
    assert rows[0]["estado"] == "interrompida" and rows[0]["invocacao_em"] is None
    e = linhas(f, "campanha_execucoes")[0]
    if mutacao in {"optout", "contato", "telefone", "lista"}:
        assert e["estado"] == "concluida"
    else:
        assert e["estado"] == ("interrompida" if mutacao == "revogacao" else "pausada")


@pytest.mark.parametrize("momento", ["reserva", "marcador", "barreira", "resposta", "resultado"])
def test_falhas_e_recuperacao_idempotente(operacional, monkeypatch, momento):
    f, receipt, ctx, executor, adm = operacional
    chamadas = []
    invocar = executor._invocar

    def mock(*args):
        chamadas.append(True)
        return invocar(*args)

    monkeypatch.setattr(executor, "_invocar", mock)
    method = {
        "reserva": "_reservar",
        "marcador": "_marcar",
        "barreira": "_marcar",
        "resposta": "_invocar",
        "resultado": "_resultado",
    }[momento]
    original = getattr(executor, method)

    def falhar(*args):
        if momento == "resultado":
            # Falha depois de todas as escritas, antes do commit real.
            def auditar(*_a, **_k):
                raise OperationalError("falha", None, None)

            with monkeypatch.context() as m:
                m.setattr(executor, "_auditar", auditar)
                return original(*args)
        if momento == "marcador":

            def commit(_):
                raise OperationalError("falha", None, None)

            event.listen(f["db"].engine, "commit", commit)
            try:
                return original(*args)
            finally:
                event.remove(f["db"].engine, "commit", commit)
        result = original(*args)
        if momento == "barreira":
            ctx.fechar()
            return result
        raise NegocioError("falha", "Indisponivel", 503)

    monkeypatch.setattr(executor, method, falhar)
    if momento in {"barreira", "resposta"}:
        assert executor.passo(adm)["estado"] == "desconhecida"
    else:
        with pytest.raises(NegocioError):
            executor.passo(adm)
        assert ctx.suspenso.is_set()
    ctx.fechar()
    _, novo = integrar(f)
    novo.recuperar()
    antes = b1.anterior.snapshot(f)
    assert novo.recuperar()["reconciliadas"] == 0
    assert b1.anterior.snapshot(f) == antes
    tentativa = linhas(f, "tentativas_envio")[0]
    assert tentativa["estado"] == (
        "interrompida" if momento in {"reserva", "marcador"} else "desconhecida"
    )
    assert len(chamadas) == int(momento in {"resposta", "resultado"})
    with pytest.raises(NegocioError):
        novo.passo(adm)
    assert b1.anterior.snapshot(f) == antes


@pytest.mark.parametrize("mudanca", ["pausa", "revogacao", "logout", "expiracao", "encerramento"])
def test_resultado_admitido_independe_da_sessao_e_pausa(operacional, monkeypatch, mudanca):
    f, receipt, ctx, executor, adm = operacional
    original = executor._invocar

    def durante(*args):
        if mudanca == "pausa":
            assert b1.pausa(f, receipt)["estado_execucao"] == "pausando"
        elif mudanca == "revogacao":
            b1.revogar(f)
        elif mudanca == "logout":
            f["app"].state.auth.logout(f["token"])
        elif mudanca == "expiracao":
            clock = f["app"].state.auth.clock
            monkeypatch.setattr(f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9))
        else:
            ctx.fechar()
        return original(*args)

    monkeypatch.setattr(executor, "_invocar", durante)
    assert executor.passo(adm)["estado"] == "aceita_provedor"
    assert linhas(f, "tentativas_envio")[0]["estado"] == "aceita_provedor"
    assert linhas(f, "campanha_execucoes")[0]["estado"] == (
        "interrompida" if mudanca == "revogacao" else "concluida"
    )


@pytest.mark.parametrize(
    "fase", ["sem_tentativa", "reserva", "marcador", "resultado", "pausa", "revogacao"]
)
def test_recuperacao_nao_reconstroi_resultados_nem_rearma(operacional, monkeypatch, fase):
    f, receipt, ctx, executor, adm = operacional
    tid = None
    if fase != "sem_tentativa":
        tid = executor._reservar(adm)
        if fase != "reserva":
            executor._marcar(adm, tid)
        if fase == "resultado":
            executor._resultado(adm, tid, "falhou", "falha")
        if fase == "pausa":
            b1.pausa(f, receipt)
        if fase == "revogacao":
            b1.revogar(f)
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Recuperacao nao invoca"))
    antes = linhas(f, "tentativas_envio")
    executor.recuperar()
    depois = b1.anterior.snapshot(f)
    assert executor.recuperar()["reconciliadas"] == 0
    assert b1.anterior.snapshot(f) == depois
    assert linhas(f, "campanha_execucoes")[0]["estado"] == {
        "resultado": "concluida_com_falhas",
        "revogacao": "interrompida",
    }.get(fase, "pausada")
    if fase == "resultado":
        assert linhas(f, "tentativas_envio") == antes
    elif tid:
        assert linhas(f, "tentativas_envio")[0]["estado"] == (
            "interrompida" if fase == "reserva" else "desconhecida"
        )
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b1.anterior.snapshot(f) == depois


def test_retomada_explicita_troca_posse_e_rejeita_resultado_antigo(operacional):
    f, receipt, ctx, executor, adm = operacional
    tid = executor._reservar(adm)
    executor._marcar(adm, tid)
    executor.recuperar()
    antes = b1.anterior.snapshot(f)
    with pytest.raises(NegocioError):
        executor._resultado(adm, tid, "aceita_provedor", "aceita")
    assert b1.anterior.snapshot(f) == antes
    ctx.fechar()
    _, novo = integrar(f)
    row = linhas(f, "campanha_execucoes")[0]
    newadm = admitir(f, novo, {**receipt, "versao_execucao": row["versao"]}, retomar=True)
    assert newadm.geracao > adm.geracao and newadm.proprietario_id != adm.proprietario_id
    assert not novo.passo(newadm)["processado"]  # Desconhecida nunca volta a fila.
    assert len(linhas(f, "tentativas_envio")) == 1


def test_privacidade_sem_rede_dns_subprocesso_ou_credenciais(operacional, monkeypatch, caplog):
    f, _, _, executor, adm = operacional

    def proibido(*_, **__):
        pytest.fail("I/O externo proibido")

    monkeypatch.setattr(socket.socket, "connect", proibido)
    monkeypatch.setattr(socket, "getaddrinfo", proibido)
    monkeypatch.setattr(subprocess, "Popen", proibido)
    for nome in ("assinatura", "gravar", "recuperar", "limpar", "pendente"):
        monkeypatch.setattr(f["app"].state.contas_envio.segredos, nome, proibido)
    result = executor.passo(adm)
    audit = json.dumps(linhas(f, "auditoria"), default=str)
    operational = "".join(
        json.dumps(linhas(f, t), default=str)
        for t in (
            "campanha_execucoes",
            "campanha_execucao_itens",
            "tentativas_envio",
            "campanha_execucao_transicoes",
        )
    )
    for segredo in (f["token"], f["simulavel"][2], "+5511912345678", f["r"][3]["nome"]):
        assert segredo not in str(result) + repr(adm) + audit + operational + caplog.text


def test_optout_concorrente_sinaliza_antes_do_lock_e_falha_mantem_suspensao(
    operacional, monkeypatch
):
    f, _, ctx, executor, adm = operacional
    sinalizado = Event()
    original = ctx.proteger_optout
    _, _, svc, contato, escopo, identidade, token = f["r"][-1]
    versao = svc.estado(contato["id"], escopo["id"])["versao"]
    data = b1.anterior.consent.entrada(escopo, identidade, versao, "revogou")

    from contextlib import contextmanager

    @contextmanager
    def sinalizar(operacao_id):
        with original(operacao_id):
            sinalizado.set()
            yield

    monkeypatch.setattr(ctx, "proteger_optout", sinalizar)

    def falhar(_conn):
        raise OperationalError("falha", None, None)

    event.listen(f["db"].engine, "commit", falhar)
    try:
        with ThreadPoolExecutor(1) as pool:
            with f["svc"].repo.lock:
                pending = pool.submit(svc.registrar, contato["id"], data, token)
                assert sinalizado.wait(10)
                with pytest.raises(NegocioError):
                    ctx.barreira()
            with pytest.raises(NegocioError):
                pending.result(timeout=20)
    finally:
        event.remove(f["db"].engine, "commit", falhar)
    assert ctx.suspenso.is_set()
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert linhas(f, "tentativas_envio") == []

    # Fechar/recriar contexto nao pode apagar opt-out que falhou ao persistir.
    ctx.fechar()
    novo, _ = integrar(f)
    with pytest.raises(NegocioError):
        novo.barreira()
    svc.registrar(contato["id"], data, token)  # Mesmo UUID/corpo, commit confirmado.
    # A confirmacao SQL nao reabre a admissao encerrada nesta abertura por falha.
    with pytest.raises(NegocioError):
        novo.barreira()
    assert f["app"].state.execution_runtime._closed.is_set()
    assert linhas(f, "consentimento_suspensoes")[0]["confirmado_em"] is not None
    assert linhas(f, "tentativas_envio") == []


@pytest.mark.parametrize("logout", [False, True])
def test_sessao_revalidada_apos_esperar_lock(operacional, monkeypatch, logout):
    f, _, _, executor, adm = operacional
    lock, main, waiting = f["svc"].repo.lock, get_ident(), Event()

    class Observado:
        def acquire(self, *args, **kwargs):
            if get_ident() != main:
                waiting.set()
            return lock.acquire(*args, **kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    monkeypatch.setattr(f["svc"].repo, "lock", Observado())
    with ThreadPoolExecutor(1) as pool:
        with lock:
            pending = pool.submit(executor.passo, adm)
            assert waiting.wait(10)
            if logout:
                f["app"].state.auth.logout(f["token"])
            else:
                clock = f["app"].state.auth.clock
                monkeypatch.setattr(
                    f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9)
                )
        with pytest.raises(NegocioError):
            pending.result(timeout=20)
    assert linhas(f, "tentativas_envio") == []


def test_recuperacao_reverte_diario_posse_e_resultado_ao_falhar(operacional, monkeypatch):
    f, _, _, executor, adm = operacional
    tid = executor._reservar(adm)
    executor._marcar(adm, tid)
    antes = b1.anterior.snapshot(f)
    original = executor._auditar

    def falhar(*_, **__):
        raise OperationalError("falha", None, None)

    monkeypatch.setattr(executor, "_auditar", falhar)
    with pytest.raises(NegocioError):
        executor.recuperar()
    assert b1.anterior.snapshot(f) == antes
    monkeypatch.setattr(executor, "_auditar", original)
    assert executor.recuperar()["reconciliadas"] == 1
    assert linhas(f, "tentativas_envio")[0]["estado"] == "desconhecida"


def test_reserva_abandonada_retomada_explicita_nova_tentativa(operacional):
    f, receipt, ctx, executor, adm = operacional
    executor._reservar(adm)
    executor.recuperar()
    ctx.fechar()
    _, novo = integrar(f)
    row = linhas(f, "campanha_execucoes")[0]
    newadm = admitir(f, novo, {**receipt, "versao_execucao": row["versao"]}, retomar=True)
    assert novo.passo(newadm)["estado"] == "aceita_provedor"
    tentativas = sorted(linhas(f, "tentativas_envio"), key=lambda t: t["numero"])
    assert [t["numero"] for t in tentativas] == [1, 2]
    assert [t["estado"] for t in tentativas] == ["interrompida", "aceita_provedor"]
    assert tentativas[0]["invocacao_em"] is None


def test_fotografia_falha_criptografica_real_nao_admite_chamada(operacional, monkeypatch):
    f, _, _, executor, adm = operacional
    original = executor.p._header

    def adulterar(*args):
        header = original(*args)
        header["preparacao_id"] = str(uuid4())  # AAD diverge: falha AEAD real, sem DDL alterado.
        return header

    monkeypatch.setattr(executor.p, "_header", adulterar)
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Nao invocar"))
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert linhas(f, "tentativas_envio")[0]["estado"] == "reservada"
    assert linhas(f, "tentativas_envio")[0]["invocacao_em"] is None


def test_falha_persistencia_na_barreira_suspende_mesmo_gravando_desconhecida(
    operacional, monkeypatch
):
    f, _, ctx, executor, adm = operacional
    original = executor._marcar
    falhas = []

    def falhar_uma_vez(_):
        if not falhas:
            falhas.append(True)
            raise OperationalError("falha", None, None)

    def marcar(*args):
        result = original(*args)
        event.listen(f["db"].engine, "commit", falhar_uma_vez)
        return result

    monkeypatch.setattr(executor, "_marcar", marcar)
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Barreira falhou"))
    try:
        assert executor.passo(adm)["estado"] == "desconhecida"
    finally:
        event.remove(f["db"].engine, "commit", falhar_uma_vez)
    assert ctx.suspenso.is_set()
    antes = b1.anterior.snapshot(f)
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b1.anterior.snapshot(f) == antes


def test_perda_do_lock_de_instancia_bloqueia_despacho(operacional):
    f, _, _, executor, adm = operacional
    f["app"].state.persistence.lock.release()
    antes = b1.anterior.snapshot(f)
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b1.anterior.snapshot(f) == antes
