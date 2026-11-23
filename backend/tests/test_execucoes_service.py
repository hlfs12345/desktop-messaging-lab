"""B1 transacional em SQLite descartavel; comandos nao invocam o mock."""

import json
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, get_ident
from uuid import UUID, uuid4

import pytest
import test_autorizacoes_simulacao as anterior
import test_execucoes_migration as persistencia
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.auth.errors import AuthError
from app.campanhas import preparacoes as preparacoes_mod
from app.campanhas.execucoes import ExecucoesService
from app.campanhas.execucoes_models import ExecucaoCampanha
from app.campanhas.execucoes_schemas import IniciarExecucaoEntrada, PausarExecucaoEntrada
from app.campanhas.schemas import RevogarSimulacaoEntrada, TransicaoEntrada
from app.consentimentos.schemas import IdentidadeEntrada
from app.contas_envio.schemas import ContaEdicao
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria
from app.provedores.mock import MockProvedor

cadastro = anterior.cadastro
consentimento = anterior.consentimento
rascunho = anterior.rascunho
pronta = anterior.pronta
simulavel = anterior.simulavel
confirmada = anterior.confirmada


def contexto(f):
    return {
        **f,
        "svc": ExecucoesService(f["app"].state.autorizacoes_simulacao),
        "cid": f["root"].split("/")[-1],
        "token": f["r"][-2],
        "db": f["app"].state.persistence.database,
    }


@pytest.fixture
def autorizada(confirmada):
    receipt, _ = anterior.autorizar(confirmada)
    return {**contexto(confirmada), "autorizacao": receipt}


def revisar(f, token=None):
    return f["svc"].revisao(f["cid"], f["pid"], token or f["token"])


def entrada(f, revisao=None, **changes):
    r = revisar(f) if revisao is None else revisao
    return IniciarExecucaoEntrada(
        **{
            "operacao_id": uuid4(),
            "versao": r["versao"],
            "autorizacao_id": r["autorizacao_id"],
            "versao_autorizacao": r["versao_autorizacao"],
            "impressao": r["impressao"],
            "resumo_configuracao": r["resumo_configuracao"],
            "marcador_inicio": r["marcador_inicio"],
            "confirmo_inicio": True,
            **changes,
        }
    )


def iniciar(f, data=None, token=None):
    return f["svc"].iniciar(
        f["cid"], f["pid"], entrada(f) if data is None else data, token or f["token"]
    )


def consulta(f, recibo=None):
    return f["svc"].consultar(
        f["cid"],
        f["token"],
        **({"execucao_id": recibo["execucao_id"]} if recibo else {"preparacao_id": f["pid"]}),
    )


def pausa(f, recibo, data=None):
    data = data or PausarExecucaoEntrada(operacao_id=uuid4(), versao=recibo["versao_execucao"])
    return f["svc"].pausar(f["cid"], recibo["execucao_id"], data, f["token"])


def retorno(f, **changes):
    campanha = f["c"].get(f["root"]).json()
    return TransicaoEntrada(
        operacao_id=uuid4(), versao=campanha["versao"], preparacao_id=UUID(f["pid"]), **changes
    )


def retornar(f, data=None):
    return f["app"].state.preparacoes.transicao(f["cid"], data or retorno(f), f["token"])


def revogar(f):
    return f["svc"].a.revogar(
        f["cid"],
        f["autorizacao"]["autorizacao_id"],
        RevogarSimulacaoEntrada(operacao_id=uuid4(), versao=1, motivo_codigo="revogacao_manual"),
        f["token"],
    )


def test_revisao_sem_efeitos_inicio_atomico_pausa_e_replay(autorizada):
    f = autorizada
    antes = anterior.snapshot(f)
    r = revisar(f)
    assert r == revisar(f) and r["iniciavel"]
    assert r["marcador_inicio"].startswith("inicio-v1.")
    assert r["avaliacao_atual"]["elegiveis_atuais"] == 1
    assert not r["processamento_disponivel"]
    assert consulta(f)["execucao"] is None
    assert anterior.snapshot(f) == antes
    data = entrada(f, r)
    receipt = iniciar(f, data)
    assert receipt["versao"] == r["versao"] + 1
    assert receipt["estado_execucao"] == "executando" and receipt["versao_execucao"] == 1
    assert receipt["recibo_historico"] and receipt["validade_atual"] == "nao_avaliada"
    assert (
        receipt["incluidos"] == receipt["elegiveis_inicio"] == 1
        and receipt["ignorados_inicio"] == 0
    )
    snap = anterior.snapshot(f)
    assert iniciar(f, data) == receipt and anterior.snapshot(f) == snap
    p = PausarExecucaoEntrada(operacao_id=uuid4(), versao=1)
    stopped = pausa(f, receipt, p)
    assert stopped["estado_execucao"] == "pausada" and stopped["versao_execucao"] == 2
    snap = anterior.snapshot(f)
    assert pausa(f, receipt, p) == stopped and anterior.snapshot(f) == snap
    assert iniciar(f, data) == receipt  # Recibo antigo nao reabre execucao.
    atual = consulta(f, receipt)
    assert atual["execucao"]["estado"] == "pausada" and atual["contagens"]["pendente"] == 1
    assert anterior.snapshot(f)["tentativas_envio"] == []
    # D1 expoe a rota; corpo ausente continua sem efeitos sobre o recibo B1.
    assert f["c"].post(f["root"] + "/preparacoes/" + f["pid"] + "/execucoes").status_code == 422
    assert anterior.snapshot(f) == snap


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmo_inicio": False},
        {"confirmo_inicio": 1},
        {"confirmo_inicio": "true"},
        {"confirmo_inicio": None},
        {"versao": True},
        {"versao": 0},
        {"versao_autorizacao": 0},
        {"marcador_inicio": "autorizacao-v1." + "a" * 64},
        {"impressao": "g" * 64},
        {"texto": "dado-privado"},
    ],
)
def test_entrada_estrita_e_imutavel_sem_efeitos(autorizada, changes):
    f = autorizada
    antes = anterior.snapshot(f)
    with pytest.raises(ValidationError):
        entrada(f, **changes)
    data = entrada(f)
    with pytest.raises(ValidationError):
        data.confirmo_inicio = False
    with pytest.raises(ValidationError):
        iniciar(f, data.model_copy(update={"confirmo_inicio": False}))
    assert anterior.snapshot(f) == antes


@pytest.mark.parametrize(
    "changes",
    [
        {"marcador_inicio": "inicio-v1." + "0" * 64},
        {"versao": 999},
        {"versao_autorizacao": 2},
        {"autorizacao_id": uuid4()},
        {"impressao": "0" * 64},
        {"resumo_configuracao": "0" * 64},
    ],
)
def test_identificadores_versoes_e_marcadores_sao_revalidados(autorizada, changes):
    f = autorizada
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError) as erro:
        iniciar(f, entrada(f, **changes))
    assert erro.value.status == 409 and anterior.snapshot(f) == antes


def test_marcador_vincula_sessao_e_abertura(autorizada):
    f = autorizada
    data = entrada(f)
    from app.auth.schemas import LoginInput

    nova = (
        f["app"]
        .state.auth.login(LoginInput(login="teste", senha="frase fictícia de teste 489!"))
        .token
    )
    assert revisar(f, nova)["marcador_inicio"] != data.marcador_inicio
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        iniciar(f, data, nova)
    f["token"] = nova
    data_nova = entrada(f)
    outro = ExecucoesService(f["app"].state.autorizacoes_simulacao)
    with pytest.raises(NegocioError):
        outro.iniciar(f["cid"], f["pid"], data_nova, nova)
    assert anterior.snapshot(f) == antes


@pytest.mark.parametrize(
    "mutacao", ["revogacao", "optout", "conta", "retorno", "auditoria", "operacional"]
)
def test_marcador_obsoleto_e_zero_elegiveis_nao_criam_fila(autorizada, mutacao):
    f = autorizada
    data = entrada(f)
    if mutacao == "revogacao":
        anterior.revogar(f, f["autorizacao"])
    elif mutacao == "optout":
        anterior.bloquear(f, "revogou")
    elif mutacao == "conta":
        conta = f["c"].get(f["root"]).json()["conta_envio_id"]
        f["app"].state.contas_envio.editar(
            conta, ContaEdicao(operacao_id=uuid4(), versao=1, ativo=False), f["token"]
        )
    elif mutacao == "retorno":
        retornar(f)
    elif mutacao == "auditoria":
        with f["svc"].repo.transacao() as session:
            f["svc"].repo.auditar(session, None, "outra_operacao", "campanha", f["cid"])
    else:
        with f["db"].engine.begin() as conn:
            values = dict(
                conn.exec_driver_sql("SELECT * FROM campanha_autorizacoes_simulacao")
                .mappings()
                .one()
            )
            persistencia.criar_execucao(conn, values)
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError) as erro:
        iniciar(f, data)
    assert erro.value.status == 409 and anterior.snapshot(f) == antes
    if mutacao in {"revogacao", "optout", "conta"}:
        r = revisar(f)
        assert not r["iniciavel"]
        with pytest.raises(NegocioError):
            iniciar(f, entrada(f, r))
        assert anterior.snapshot(f) == antes


@pytest.mark.parametrize(
    "falha",
    ["chave", "integridade", "configuracao", "sem_autorizacao", "nao_confirmada", "outra_campanha"],
)
def test_fotografia_e_autorizacao_obrigatorias(confirmada, monkeypatch, falha):
    f = contexto(confirmada)
    if falha != "sem_autorizacao":
        receipt, _ = anterior.autorizar(f)
        f["autorizacao"] = receipt
    if falha == "chave":
        monkeypatch.setattr(
            preparacoes_mod,
            "abrir",
            lambda _: (_ for _ in ()).throw(preparacoes_mod.indisponivel()),
        )
    elif falha == "integridade":
        monkeypatch.setattr(
            f["svc"].p,
            "_autenticar_fotografia",
            lambda *_: (_ for _ in ()).throw(
                NegocioError("integridade_comprometida", "Fotografia indisponivel.", 503)
            ),
        )
    elif falha == "configuracao":
        original = f["svc"].p._autenticar_fotografia

        def real(*args):
            header, key, rev = original(*args)
            header = {**header, "provedor": "meta_cloud"}
            return header, key, rev

        monkeypatch.setattr(f["svc"].p, "_autenticar_fotografia", real)
    elif falha == "nao_confirmada":
        # Isolar validacao de vigencia sem alterar fotografias no banco.
        original = f["svc"].p._obter

        def nao_confirmada(*args):
            p = original(*args)
            from types import SimpleNamespace

            return SimpleNamespace(id=p.id, invalidado_em=None, confirmado_em=None)

        monkeypatch.setattr(f["svc"].p, "_obter", nao_confirmada)
    elif falha == "outra_campanha":
        f["cid"] = str(uuid4())
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        revisar(f)
    assert anterior.snapshot(f) == antes


@pytest.mark.parametrize("diferenca", ["versao", "marcador", "autorizacao", "operacao"])
def test_replay_divergente_ou_novo_uuid_nao_duplica(autorizada, diferenca):
    f = autorizada
    data = entrada(f)
    iniciar(f, data)
    changes = {
        "versao": {"versao": data.versao + 1},
        "marcador": {"marcador_inicio": "inicio-v1." + "a" * 64},
        "autorizacao": {"autorizacao_id": uuid4()},
        "operacao": {"operacao_id": uuid4()},
    }[diferenca]
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        iniciar(f, data.model_copy(update=changes))
    assert anterior.snapshot(f) == antes


def test_replay_persistente_apos_retorno_sem_fotografia(autorizada, monkeypatch):
    f = autorizada
    data = entrada(f)
    receipt = iniciar(f, data)
    parar = PausarExecucaoEntrada(operacao_id=uuid4(), versao=1)
    stopped = pausa(f, receipt, parar)
    returned = retornar(f)
    novo = ExecucoesService(f["app"].state.autorizacoes_simulacao)

    def proibido(*_):
        pytest.fail("Replay abriu fotografia")

    monkeypatch.setattr(novo, "_avaliar", proibido)
    antes = anterior.snapshot(f)
    assert novo.iniciar(f["cid"], f["pid"], data, f["token"]) == receipt
    assert novo.pausar(f["cid"], receipt["execucao_id"], parar, f["token"]) == stopped
    assert anterior.snapshot(f) == antes
    assert consulta(f, receipt)["execucao"]["estado"] == "interrompida"
    assert f["c"].get(f["root"]).json()["versao"] == returned["versao"]


@pytest.mark.parametrize("tipo", ["mesmo", "divergente", "uuid_distinto"])
def test_inicios_concorrentes_sem_execucao_parcial(autorizada, tipo):
    f = autorizada
    data = entrada(f)
    other = (
        data
        if tipo == "mesmo"
        else data.model_copy(
            update={"impressao": "0" * 64} if tipo == "divergente" else {"operacao_id": uuid4()}
        )
    )
    barrier = Barrier(2)

    def disputar(body):
        barrier.wait(timeout=10)
        try:
            return iniciar(f, body)
        except NegocioError as erro:
            return erro.status

    with ThreadPoolExecutor(2) as pool:
        resultados = list(pool.map(disputar, [data, other]))
    if tipo == "mesmo":
        assert resultados[0] == resultados[1]
    else:
        assert sum(isinstance(r, dict) for r in resultados) == 1 and 409 in resultados
    snap = anterior.snapshot(f)
    assert len(snap["campanha_execucoes"]) == len(snap["campanha_execucao_itens"]) == 1
    assert snap["tentativas_envio"] == []


@pytest.mark.parametrize("mutacao", ["logout", "expiracao", "revogacao", "optout", "retorno"])
def test_inicio_revalida_depois_de_esperar_lock(autorizada, monkeypatch, mutacao):
    f = autorizada
    data, voltar = entrada(f), retorno(f)
    repo = f["svc"].repo
    lock, main, waiting = repo.lock, get_ident(), Event()

    class Observado:
        def acquire(self, blocking=True, timeout=-1):
            if get_ident() != main:
                waiting.set()
            return lock.acquire(blocking, timeout)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    monkeypatch.setattr(repo, "lock", Observado())
    with ThreadPoolExecutor(1) as pool:
        with lock:
            pending = pool.submit(iniciar, f, data)
            assert waiting.wait(10)
            if mutacao == "logout":
                f["app"].state.auth.logout(f["token"])
            elif mutacao == "expiracao":
                clock = f["app"].state.auth.clock
                monkeypatch.setattr(
                    f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9)
                )
            elif mutacao == "revogacao":
                revogar(f)
            elif mutacao == "optout":
                anterior.bloquear(f, "revogou")
            else:
                retornar(f, voltar)
        with pytest.raises(AuthError if mutacao in {"logout", "expiracao"} else NegocioError):
            pending.result(timeout=20)
    snap = anterior.snapshot(f)
    assert (
        snap["campanha_execucoes"]
        == snap["campanha_execucao_itens"]
        == snap["tentativas_envio"]
        == []
    )


@pytest.mark.parametrize("etapa", ["antes_auditoria", "depois_auditoria", "commit", "sessao_final"])
def test_rollback_real_apos_fila_e_auditoria(autorizada, monkeypatch, etapa):
    f = autorizada
    data = entrada(f)
    antes = anterior.snapshot(f)
    original = f["svc"]._recibo
    vistos = []

    def recibo(session, *args, **kwargs):
        assert session.scalar(select(ExecucaoCampanha.id)) is not None
        vistos.append("fila")
        if etapa == "antes_auditoria":
            raise OperationalError("falha", None, None)
        result = original(session, *args, **kwargs)
        assert session.get(Auditoria, str(data.operacao_id)) is not None
        if etapa == "depois_auditoria":
            raise OperationalError("falha", None, None)
        if etapa == "sessao_final":
            clock = f["app"].state.auth.clock
            monkeypatch.setattr(f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9))
        return result

    monkeypatch.setattr(f["svc"], "_recibo", recibo)

    def falhar_commit(_):
        raise OperationalError("falha", None, None)

    if etapa == "commit":
        event.listen(f["db"].engine, "commit", falhar_commit)
    try:
        with pytest.raises(AuthError if etapa == "sessao_final" else NegocioError):
            iniciar(f, data)
    finally:
        if etapa == "commit":
            event.remove(f["db"].engine, "commit", falhar_commit)
    assert vistos and anterior.snapshot(f) == antes


def test_pausa_idempotente_sem_duplicar_transicoes_e_sem_chave(autorizada, monkeypatch):
    f = autorizada
    receipt = iniciar(f)

    def proibido(*_):
        pytest.fail("Pausa abriu fotografia")

    monkeypatch.setattr(f["svc"].a, "_avaliar", proibido)
    stopped = pausa(f, receipt)
    antes = anterior.snapshot(f)
    novo = pausa(f, stopped)
    depois = anterior.snapshot(f)
    assert novo["estado_execucao"] == "pausada" and novo["versao_execucao"] == 2
    for tabela in (
        "campanha_execucoes",
        "campanha_execucao_itens",
        "campanha_execucao_transicoes",
        "tentativas_envio",
    ):
        assert depois[tabela] == antes[tabela]
    with pytest.raises(NegocioError):
        pausa(f, receipt)


@pytest.mark.parametrize("iniciada", [False, True])
def test_pausa_com_tentativa_solicita_parada_sem_cancelar_chamada(autorizada, iniciada):
    f = autorizada
    receipt = iniciar(f)
    with f["db"].engine.begin() as conn:
        e = dict(conn.exec_driver_sql("SELECT * FROM campanha_execucoes").mappings().one())
        t = persistencia.reservar(conn, e)
        if iniciada:
            persistencia.iniciar(conn, t)
    stopped = pausa(f, receipt)
    assert stopped["estado_execucao"] == "pausando"
    assert consulta(f, receipt)["contagens"]["processando" if iniciada else "reservado"] == 1
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        retornar(f)
    assert anterior.snapshot(f) == antes


@pytest.mark.parametrize("etapa", ["encerramento", "revogacao", "invalidacao"])
def test_retorno_ao_rascunho_atomico_inclusive_auditorias(autorizada, monkeypatch, etapa):
    f = autorizada
    receipt = iniciar(f)
    data = retorno(f)
    antes = anterior.snapshot(f)
    original = f["svc"].repo.auditar

    def auditar(session, usuario, acao, *args, **kwargs):
        original(session, usuario, acao, *args, **kwargs)
        session.flush()
        if acao == (
            "encerrar_execucao_para_rascunho" if etapa == "encerramento" else "revogar_simulacao"
        ):
            raise OperationalError("falha", None, None)

    def falhar(_conn, _cursor, sql, *_):
        if sql.startswith("UPDATE campanha_preparacoes"):
            raise OperationalError("falha", None, None)

    if etapa != "invalidacao":
        monkeypatch.setattr(f["svc"].repo, "auditar", auditar)
    else:
        event.listen(f["db"].engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(NegocioError):
            retornar(f, data)
    finally:
        if etapa == "invalidacao":
            event.remove(f["db"].engine, "after_cursor_execute", falhar)
    assert anterior.snapshot(f) == antes
    monkeypatch.setattr(f["svc"].repo, "auditar", original)
    result = retornar(f, data)
    snap = anterior.snapshot(f)
    assert retornar(f, data) == result and anterior.snapshot(f) == snap
    assert consulta(f, receipt)["execucao"]["estado"] == "interrompida"
    assert consulta(f, receipt)["contagens"]["interrompido"] == 1


def test_privacidade_e_ausencia_de_invocacao(autorizada, monkeypatch, caplog):
    f = autorizada

    def proibido(*_, **__):
        pytest.fail("Comando acessou provedor, rede ou credenciais")

    for method in ("__init__", "executar"):
        monkeypatch.setattr(MockProvedor, method, proibido)
    monkeypatch.setattr(socket.socket, "connect", proibido)
    monkeypatch.setattr(socket, "getaddrinfo", proibido)
    monkeypatch.setattr(subprocess, "Popen", proibido)
    for nome in ("assinatura", "gravar", "recuperar", "limpar", "pendente"):
        monkeypatch.setattr(f["app"].state.contas_envio.segredos, nome, proibido)
    original = preparacoes_mod.GCM.decrypt

    def decifrar(self, nonce, cipher, aad):
        contexto = json.loads(aad)
        if isinstance(contexto, dict) and contexto.get("finalidade") == "texto":
            proibido()
        return original(self, nonce, cipher, aad)

    monkeypatch.setattr(preparacoes_mod.GCM, "decrypt", decifrar)
    review = revisar(f)
    receipt = iniciar(f, entrada(f, review))
    pausa(f, receipt)
    result = consulta(f, receipt)
    retornar(f)
    with f["db"].engine.begin() as conn:
        audit = "\n".join(
            conn.exec_driver_sql(
                "SELECT resumo_json FROM auditoria WHERE acao IN "
                "('iniciar_execucao','pausar_execucao','encerrar_execucao_para_rascunho')"
            ).scalars()
        )
    output = str(review) + str(receipt) + str(result) + audit + caplog.text
    for privado in (f["token"], f["simulavel"][2], f["r"][3]["nome"], "+5511912345678"):
        assert privado not in output


@pytest.mark.parametrize("incluida", [False, True])
def test_fila_preserva_fotografia_e_ignora_impedidos_sem_promover_excluidos(simulavel, incluida):
    r, cid, _ = simulavel
    c, app, _, contato, original, _, token, cf = r
    telefone = c.post(
        "/contatos/" + contato["id"] + "/telefones", json={"telefone": "+442083661177"}
    ).json()
    identidade = app.state.consentimentos.criar_identidade(
        contato["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(telefone["id"]),
            canal="whatsapp",
            versao=app.state.consentimentos.estado(contato["id"], cf[4]["id"])["versao"],
        ),
        token,
    )
    cf2 = (*cf[:5], identidade, token)
    if incluida:
        anterior.consent.registrar(cf2)
    response = anterior.sim.prep.campanhas.selecionar(
        c, c.get("/campanhas/" + cid).json(), identidade
    )
    assert response.status_code == 200
    f = anterior.confirmar(simulavel)
    auth, _ = anterior.autorizar(f)
    f = {**contexto(f), "autorizacao": auth}
    obsoleto = entrada(f)
    if incluida:
        anterior.bloquear(f, "revogou")
    else:
        anterior.consent.registrar(cf2)
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        iniciar(f, obsoleto)
    assert anterior.snapshot(f) == antes
    review = revisar(f)
    assert review["iniciavel"] and review["avaliacao_atual"]["elegiveis_atuais"] == 1
    result = iniciar(f, entrada(f, review))
    assert result["ignorados_inicio"] == int(incluida)
    with f["db"].engine.begin() as conn:
        itens = conn.exec_driver_sql(
            "SELECT identidade_contato_id,estado,ordem,preparacao_id "
            "FROM campanha_execucao_itens ORDER BY ordem"
        ).all()
        expected = sorted([original["id"], identidade["id"]] if incluida else [original["id"]])
        assert [i[0] for i in itens] == expected
        assert [i[2] for i in itens] == list(range(1, len(expected) + 1))
        assert all(i[3] == f["pid"] for i in itens)
        assert dict((i[0], i[1]) for i in itens) == (
            {original["id"]: "ignorado", identidade["id"]: "pendente"}
            if incluida
            else {original["id"]: "pendente"}
        )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    depois = anterior.snapshot(f)
    for tabela in (
        "campanha_preparacoes",
        "campanha_preparacao_itens",
        "campanha_autorizacoes_simulacao",
    ):
        assert depois[tabela] == antes[tabela]
    retornar(f)
    counts = consulta(f, result)["contagens"]
    assert counts["ignorado"] == int(incluida) and counts["interrompido"] == 1


@pytest.mark.parametrize("mutacao", ["revogacao", "optout", "retorno"])
def test_inicio_vence_disputa_sem_impedir_revogacao_nem_retorno_seguro(
    autorizada, monkeypatch, mutacao
):
    f = autorizada
    data, voltar = entrada(f), retorno(f)
    repo = f["svc"].repo
    lock, main, waiting = repo.lock, get_ident(), Event()

    class Observado:
        def acquire(self, blocking=True, timeout=-1):
            if get_ident() != main:
                waiting.set()
            return lock.acquire(blocking, timeout)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    monkeypatch.setattr(repo, "lock", Observado())

    def alterar():
        if mutacao == "revogacao":
            return revogar(f)
        if mutacao == "optout":
            return anterior.bloquear(f, "revogou")
        return retornar(f, voltar)

    with ThreadPoolExecutor(1) as pool:
        with lock:
            pending = pool.submit(alterar)
            assert waiting.wait(10)
            result = iniciar(f, data)
        if mutacao == "retorno":
            with pytest.raises(NegocioError):
                pending.result(timeout=20)
        else:
            pending.result(timeout=20)
    assert consulta(f, result)["execucao"]["estado"] == "executando"
    assert anterior.snapshot(f)["tentativas_envio"] == []
    retornar(f)
    assert consulta(f, result)["execucao"]["estado"] == "interrompida"


@pytest.mark.parametrize("mesmo_uuid", [False, True])
def test_pausas_concorrentes_uma_transicao(autorizada, mesmo_uuid):
    f = autorizada
    receipt = iniciar(f)
    data = PausarExecucaoEntrada(operacao_id=uuid4(), versao=1)
    other = data if mesmo_uuid else data.model_copy(update={"operacao_id": uuid4()})
    barrier = Barrier(2)

    def disputar(body):
        barrier.wait(timeout=10)
        try:
            return pausa(f, receipt, body)
        except NegocioError as erro:
            return erro.status

    with ThreadPoolExecutor(2) as pool:
        resultados = list(pool.map(disputar, [data, other]))
    if mesmo_uuid:
        assert resultados[0] == resultados[1]
    else:
        assert sum(isinstance(r, dict) for r in resultados) == 1 and 409 in resultados
    assert consulta(f, receipt)["execucao"]["versao"] == 2
    with f["db"].engine.begin() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_execucao_transicoes "
                "WHERE entidade='execucao' AND versao=2"
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("comando", ["revisao", "inicio", "pausa", "consulta"])
def test_perfil_revalidado_em_todos_os_comandos(autorizada, monkeypatch, comando):
    f = autorizada
    data = entrada(f)
    receipt = iniciar(f) if comando in {"pausa", "consulta"} else None
    original = f["app"].state.auth.current

    def incompativel(*args, **kwargs):
        current = original(*args, **kwargs)
        return current.model_copy(
            update={"usuario": current.usuario.model_copy(update={"perfil": "incompativel"})}
        )

    monkeypatch.setattr(f["app"].state.auth, "current", incompativel)
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError) as erro:
        if comando == "revisao":
            revisar(f)
        elif comando == "inicio":
            iniciar(f, data)
        elif comando == "pausa":
            pausa(f, receipt)
        else:
            consulta(f, receipt)
    assert erro.value.status == 403 and anterior.snapshot(f) == antes


def test_replay_exige_mesmo_usuario_mas_aceita_nova_sessao_valida(autorizada, monkeypatch):
    from app.auth.schemas import LoginInput

    f = autorizada
    data = entrada(f)
    receipt = iniciar(f, data)
    f["token"] = (
        f["app"]
        .state.auth.login(LoginInput(login="teste", senha="frase fictícia de teste 489!"))
        .token
    )
    assert iniciar(f, data) == receipt
    original = f["app"].state.auth.current
    outro_usuario = str(uuid4())

    def outro(*args, **kwargs):
        current = original(*args, **kwargs)
        return current.model_copy(
            update={"usuario": current.usuario.model_copy(update={"id": outro_usuario})}
        )

    monkeypatch.setattr(f["app"].state.auth, "current", outro)
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        iniciar(f, data)
    assert anterior.snapshot(f) == antes


@pytest.mark.parametrize("etapa", ["auditoria", "sessao_final"])
def test_pausa_reverte_estado_diario_e_recibo(autorizada, monkeypatch, etapa):
    f = autorizada
    receipt = iniciar(f)
    antes = anterior.snapshot(f)
    original = f["svc"]._recibo

    def falhar(*args, **kwargs):
        result = original(*args, **kwargs)
        if etapa == "auditoria":
            raise OperationalError("falha", None, None)
        clock = f["app"].state.auth.clock
        monkeypatch.setattr(f["app"].state.auth, "clock", lambda: clock() + timedelta(hours=9))
        return result

    monkeypatch.setattr(f["svc"], "_recibo", falhar)
    with pytest.raises(AuthError if etapa == "sessao_final" else NegocioError):
        pausa(f, receipt)
    assert anterior.snapshot(f) == antes


def test_consulta_respeita_vinculos_e_seletores_e_pausa_recusa_terminal(autorizada):
    f = autorizada
    receipt = iniciar(f)
    antes = anterior.snapshot(f)
    for seletores in ({}, {"execucao_id": receipt["execucao_id"], "preparacao_id": f["pid"]}):
        with pytest.raises(NegocioError) as erro:
            f["svc"].consultar(f["cid"], f["token"], **seletores)
        assert erro.value.status == 422
    for campo in ("execucao_id", "preparacao_id"):
        with pytest.raises(NegocioError) as erro:
            f["svc"].consultar(f["cid"], f["token"], **{campo: str(uuid4())})
        assert erro.value.status == 404
    outra = anterior.sim.prep.campanhas.criar(f["r"], operacao_id=str(uuid4()))
    with pytest.raises(NegocioError) as erro:
        f["svc"].consultar(outra["id"], f["token"], execucao_id=receipt["execucao_id"])
    assert erro.value.status == 404
    assert anterior.snapshot(f)["campanha_execucoes"] == antes["campanha_execucoes"]
    retornar(f)
    atual = consulta(f, receipt)
    antes = anterior.snapshot(f)
    with pytest.raises(NegocioError):
        pausa(f, {**receipt, "versao_execucao": atual["execucao"]["versao"]})
    assert anterior.snapshot(f) == antes
