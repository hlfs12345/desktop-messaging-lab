"""B2.1: certificados reais, dados historicos intactos e adulteracoes sem invocacao."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import MappingProxyType
from uuid import uuid4

import pytest
import test_executor_mock as b2
import test_executor_mock_desempenho as desempenho

from app.campanhas.configuracao_simulacao import resumo_configuracao
from app.campanhas.execucoes_models import ItemExecucao
from app.campanhas.models import AutorizacaoSimulacao, Preparacao, PreparacaoItem
from app.consentimentos.models import IdentidadeContato
from app.contatos.errors import NegocioError
from app.contatos.models import Contato
from app.provedores.mock import MockProvedor

cadastro = b2.cadastro
consentimento = b2.consentimento
rascunho = b2.rascunho
pronta = b2.pronta
simulavel = b2.simulavel


@pytest.fixture
def certificada(simulavel):
    desempenho.popular(simulavel, 3)
    return b2.construir(simulavel)


@pytest.mark.parametrize(
    "alvo",
    [
        "conteudo",
        "nonce",
        "incluido",
        "evento",
        "motivos",
        "tamanho",
        "identidade",
        "ordem",
        "item",
        "execucao",
        "preparacao_item",
        "destinatario",
        "preparacao",
        "autorizacao",
        "configuracao",
        "prova",
        "sem_prova",
        "metadados",
        "hash_isolado",
        "outro_item",
        "chave",
        "manifesto",
        "confirmacao",
    ],
)
def test_adulteracao_nao_invoca_mock(certificada, monkeypatch, alvo):
    f, receipt, ctx, executor, adm = certificada
    with f["svc"].repo.transacao() as s:
        from sqlalchemy import select

        fila = s.scalar(
            select(ItemExecucao)
            .where(ItemExecucao.execucao_id == receipt["execucao_id"])
            .order_by(ItemExecucao.ordem)
        )
        foto = s.get(PreparacaoItem, (f["pid"], fila.identidade_contato_id))
        prep = s.get(Preparacao, f["pid"])
        auth = s.get(AutorizacaoSimulacao, receipt["autorizacao_id"])
    selo = ctx._selos[adm.execucao_id]
    certificados = dict(selo.itens)
    if alvo in {"prova", "sem_prova", "metadados", "hash_isolado", "outro_item"}:
        if alvo == "sem_prova":
            certificados.pop(fila.id)
        elif alvo == "outro_item":
            certificados[fila.id] = next(v for k, v in certificados.items() if k != fila.id)
        elif alvo == "hash_isolado":
            certificados[fila.id] = hashlib.sha256(b"hash-nao-autenticado").digest()
        elif alvo == "prova":
            certificados[fila.id] = (
                bytes([certificados[fila.id][0] ^ 1]) + certificados[fila.id][1:]
            )
        ctx._selos[adm.execucao_id] = replace(
            selo,
            cabecalho=b"x" * 32 if alvo == "metadados" else selo.cabecalho,
            itens=MappingProxyType(certificados),
        )
    elif alvo in {"preparacao", "chave", "manifesto", "confirmacao"}:
        if alvo == "preparacao":
            prep.impressao = "0" * 64
        elif alvo == "chave":
            prep.chave_cifrada = b"x" * 48
        elif alvo == "manifesto":
            prep.manifesto = b"x" * 48
        else:
            prep.confirmado_em = None
        monkeypatch.setattr(executor.p, "_obter", lambda *_: prep)
    elif alvo == "autorizacao":
        auth.impressao = "0" * 64
    elif alvo == "configuracao":
        original = executor.p._header

        def alterar(*args):
            header = original(*args)
            cfg = header["configuracao_simulacao"]
            cfg["cenario"] = "falhou"
            cfg["resumo_configuracao"] = resumo_configuracao(
                {k: v for k, v in cfg.items() if k != "resumo_configuracao"}
            )
            return header

        monkeypatch.setattr(executor.p, "_header", alterar)
    else:
        objeto, campo, valor = {
            "conteudo": (foto, "conteudo_cifrado", b"x" * len(foto.conteudo_cifrado)),
            "nonce": (foto, "nonce", b"x" * 12),
            "incluido": (foto, "incluido", 0),
            "evento": (foto, "evento_consentimento_id", str(uuid4())),
            "motivos": (foto, "motivos_json", '["adulterado"]'),
            "tamanho": (foto, "bytes_texto", foto.bytes_texto + 1),
            "identidade": (foto, "identidade_contato_id", str(uuid4())),
            "ordem": (fila, "ordem", fila.ordem + 1),
            "item": (fila, "id", str(uuid4())),
            "execucao": (fila, "execucao_id", str(uuid4())),
            "preparacao_item": (foto, "preparacao_id", str(uuid4())),
            "destinatario": (fila, "destinatario_id", str(uuid4())),
        }[alvo]
        setattr(objeto, campo, valor)

    obter = executor.repo.obter

    def substituido(s, modelo, identifier):
        if modelo is PreparacaoItem:
            return foto
        if modelo is ItemExecucao:
            return fila
        if modelo is AutorizacaoSimulacao and alvo == "autorizacao":
            return auth
        return obter(s, modelo, identifier)

    monkeypatch.setattr(executor.repo, "obter", substituido)
    monkeypatch.setattr(
        MockProvedor, "executar", lambda *_: pytest.fail("Adulteracao produziu chamada")
    )
    with pytest.raises(NegocioError):
        executor.passo(adm)
    rows = b2.linhas(f, "tentativas_envio")
    assert all(t["invocacao_em"] is None for t in rows)
    assert ctx.suspenso.is_set() and ctx._selos == {}


def test_certificados_sem_varredura_preservam_historico_e_formato(certificada, monkeypatch):
    f, receipt, ctx, executor, adm = certificada
    antes = b2.b1.anterior.snapshot(f)
    monkeypatch.setattr(
        executor.p, "_integridade", lambda *_: pytest.fail("Varredura integral durante passo")
    )
    for _ in range(3):
        assert executor.passo(adm)["estado"] == "aceita_provedor"
    depois = b2.b1.anterior.snapshot(f)
    for tabela in (
        "campanha_preparacoes",
        "campanha_preparacao_itens",
        "campanha_autorizacoes_simulacao",
    ):
        assert antes[tabela] == depois[tabela]
    assert b2.estado(f, receipt) == "concluida"
    assert "chave" not in repr(next(iter(ctx._selos.values())))
    ctx.fechar()
    assert ctx._selos == {}


def test_falta_de_selo_recusa_sem_fallback_ou_autoautenticacao(certificada, monkeypatch):
    f, _, ctx, executor, adm = certificada
    ctx._selos.clear()
    monkeypatch.setattr(
        executor.p, "_autenticar_fotografia", lambda *_: pytest.fail("Fallback implicito")
    )
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Selo ausente"))
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b2.linhas(f, "tentativas_envio")[0]["invocacao_em"] is None


def test_mudanca_de_esquema_invalida_certificado(certificada, monkeypatch):
    f, _, _, executor, adm = certificada
    # Somente base descartavel. Nenhuma mudanca em tabelas/constraints do produto.
    with f["db"].engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alteracao_externa_teste (id INTEGER)")
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Esquema mudou"))
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b2.linhas(f, "tentativas_envio")[0]["invocacao_em"] is None


@pytest.mark.parametrize("origem", ["campanha", "preparacao", "admissao"])
def test_certificado_valido_nao_e_transferivel(certificada, monkeypatch, origem):
    f, receipt, ctx, executor, adm = certificada
    selo = ctx._selos[adm.execucao_id]
    if origem == "admissao":
        nova = executor.admitir(adm.execucao_id, f["token"], 1)
        assert nova.admissao_id != adm.admissao_id
        alvo = adm.execucao_id
    else:
        sim = b2.b1.anterior.sim
        if origem == "campanha":
            b2.b1.pausa(f, receipt)
            account = f["c"].get(f["root"]).json()["conta_envio_id"]
            outra = sim.prep.campanhas.criar(
                f["r"], operacao_id=str(uuid4()), conta_envio_id=account
            )
            assert sim.prep.campanhas.selecionar(f["c"], outra, f["r"][4]).status_code == 200
            simulavel = (f["r"], outra["id"], f["simulavel"][2])
        else:
            b2.b1.retornar(f)
            simulavel = f["simulavel"]
        photo, _ = sim.preparar(simulavel)
        sim.confirmar(simulavel, photo)
        novo = {
            **f,
            "cid": simulavel[1],
            "root": "/campanhas/" + simulavel[1],
            "pid": photo["preparacao_id"],
        }
        b2.b1.anterior.autorizar(novo)
        recibo = b2.b1.iniciar(novo)
        alvo = recibo["execucao_id"]
        nova = executor.admitir(alvo, f["token"], 1)
    ctx._selos[alvo] = selo
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Selo transferido"))
    with pytest.raises(NegocioError):
        executor.passo(nova)
    assert all(t["invocacao_em"] is None for t in b2.linhas(f, "tentativas_envio"))


def test_recuperacao_exige_nova_autenticacao_e_nao_reenvia_desconhecida(certificada, monkeypatch):
    f, receipt, ctx, executor, adm = certificada
    tid = executor._reservar(adm)
    executor._marcar(adm, tid)
    executor.recuperar()
    assert ctx._selos == {}
    ctx.fechar()
    _, novo = b2.integrar(f)
    row = b2.linhas(f, "campanha_execucoes")[0]
    original, chamadas = novo.p._autenticar_fotografia, []

    def autenticar(*args):
        chamadas.append(True)
        return original(*args)

    monkeypatch.setattr(novo.p, "_autenticar_fotografia", autenticar)
    nova = novo.admitir(receipt["execucao_id"], f["token"], row["versao"], retomar=True)
    assert len(chamadas) == 1
    assert novo.passo(nova)["estado"] == "aceita_provedor"
    tentativas = b2.linhas(f, "tentativas_envio")
    assert len(tentativas) == 2 and sum(t["estado"] == "desconhecida" for t in tentativas) == 1


def test_descarte_concorrente_do_selo_entre_cabecalho_e_item_recusa(certificada, monkeypatch):
    f, _, ctx, executor, adm = certificada
    original = executor._fotografia

    def descartar(*args, **kwargs):
        result = original(*args, **kwargs)
        ctx._selos.clear()  # Mesmo efeito da suspensao por opt-out com falha.
        return result

    monkeypatch.setattr(executor, "_fotografia", descartar)
    monkeypatch.setattr(MockProvedor, "executar", lambda *_: pytest.fail("Selo descartado"))
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b2.linhas(f, "tentativas_envio")[0]["invocacao_em"] is None


@pytest.mark.parametrize("falha", ["integridade", "commit"])
def test_renovacao_falha_nao_publica_selo_nem_reutiliza_admissao(certificada, monkeypatch, falha):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    f, _, ctx, executor, adm = certificada
    antes = b2.b1.anterior.snapshot(f)

    def falhar(*_):
        raise OperationalError("falha", None, None)

    def integridade(*_):
        raise NegocioError("integridade_comprometida", "Indisponivel", 503)

    if falha == "commit":
        event.listen(f["db"].engine, "commit", falhar)
    else:
        monkeypatch.setattr(executor.p, "_autenticar_fotografia", integridade)
    try:
        with pytest.raises(NegocioError):
            executor.admitir(adm.execucao_id, f["token"], 1)
    finally:
        if falha == "commit":
            event.remove(f["db"].engine, "commit", falhar)
    assert b2.b1.anterior.snapshot(f) == antes
    assert not ctx._selos and not ctx._admissoes
    with pytest.raises(NegocioError):
        executor.passo(adm)


@pytest.mark.parametrize("mutacao", ["optout", "revogacao"])
def test_mutacao_concorrente_posterior_ao_certificado_impede_chamada(
    certificada, monkeypatch, mutacao
):
    f, _, _, executor, adm = certificada
    reservado, liberar = Event(), Event()
    original = executor._reservar

    def esperar(*args):
        result = original(*args)
        reservado.set()
        assert liberar.wait(10)
        return result

    monkeypatch.setattr(executor, "_reservar", esperar)
    monkeypatch.setattr(
        MockProvedor, "executar", lambda *_: pytest.fail("Mutacao dinamica ignorada")
    )
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(executor.passo, adm)
        try:
            assert reservado.wait(10)
            if mutacao == "revogacao":
                b2.b1.revogar(f)
            else:
                fila = next(
                    i for i in b2.linhas(f, "campanha_execucao_itens") if i["estado"] == "reservado"
                )
                with f["svc"].repo.transacao() as s:
                    ident = s.get(IdentidadeContato, fila["identidade_contato_id"])
                    contato = s.get(Contato, ident.contato_id)
                data = b2.b1.anterior.consent.entrada(
                    f["r"][-1][4], {"id": ident.id}, contato.consentimento_versao, "revogou"
                )
                f["app"].state.consentimentos.registrar(contato.id, data, f["token"])
        finally:
            liberar.set()
        assert not pending.result(timeout=20)["processado"]
    assert all(t["invocacao_em"] is None for t in b2.linhas(f, "tentativas_envio"))


def test_excluido_com_consentimento_posterior_nao_recebe_certificado(simulavel):
    from sqlalchemy import select

    desempenho.popular(simulavel, 3)
    r, _, _ = simulavel
    svc = r[1].state.consentimentos
    with r[1].state.contatos.repo.transacao() as s:
        ident = s.scalar(
            select(IdentidadeContato).where(IdentidadeContato.id != r[4]["id"]).limit(1)
        )
        contato = s.get(Contato, ident.contato_id)
    cf = (r[0], r[1], svc, {"id": contato.id}, r[-1][4], {"id": ident.id}, r[-2])
    b2.b1.anterior.consent.registrar(cf, "revogou")
    f, _, ctx, executor, adm = b2.construir(simulavel)
    assert len(ctx._selos[adm.execucao_id].itens) == 2
    b2.b1.anterior.consent.registrar(cf)  # Elegibilidade nova nao modifica a fotografia.
    for _ in range(2):
        assert executor.passo(adm)["estado"] == "aceita_provedor"
    filas = b2.linhas(f, "campanha_execucao_itens")
    assert len(filas) == 2 and all(i["identidade_contato_id"] != ident.id for i in filas)
