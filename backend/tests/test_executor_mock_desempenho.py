"""Medicoes amostradas em fotografias reais; nao e benchmark de 8.000 chamadas."""

import json
from concurrent.futures import ThreadPoolExecutor
from statistics import mean
from threading import Event, get_ident
from time import perf_counter
from uuid import uuid4

import pytest
import test_executor_mock as b2
from sqlalchemy import event, insert, select

from app.campanhas.models import CampanhaSelecao
from app.consentimentos.models import EventoConsentimento, IdentidadeContato, PreferenciaContato
from app.contatos.models import Contato, ListaContato, TelefoneContato
from app.contatos.repository import agora

cadastro = b2.cadastro
consentimento = b2.consentimento
rascunho = b2.rascunho
pronta = b2.pronta
simulavel = b2.simulavel


def popular(simulavel, total):
    r, cid, _ = simulavel
    _, app, _, _, _, lista, token, cf = r
    user, stamp = app.state.auth.current(token).usuario.id, agora()
    lotes = {
        m: []
        for m in (
            Contato,
            TelefoneContato,
            IdentidadeContato,
            ListaContato,
            EventoConsentimento,
            PreferenciaContato,
            CampanhaSelecao,
        )
    }
    with app.state.contatos.repo.transacao() as s:
        evento = dict(s.execute(select(EventoConsentimento.__table__)).mappings().first())
        for n in range(total - 1):
            contato, telefone, identidade, eid = (str(uuid4()) for _ in range(4))
            lotes[Contato].append(
                dict(
                    id=contato,
                    nome=f"Ficticio {n}",
                    nome_busca=f"ficticio {n}",
                    ativo=1,
                    versao=1,
                    criado_em=stamp,
                    atualizado_em=stamp,
                )
            )
            lotes[TelefoneContato].append(
                dict(
                    id=telefone,
                    contato_id=contato,
                    telefone_e164=f"+55119{n:08}",
                    ativo=1,
                    versao=1,
                    criado_em=stamp,
                    atualizado_em=stamp,
                )
            )
            lotes[IdentidadeContato].append(
                dict(
                    id=identidade,
                    contato_id=contato,
                    telefone_id=telefone,
                    canal="whatsapp",
                    criado_por_usuario_id=user,
                    criado_em=stamp,
                )
            )
            lotes[ListaContato].append(
                dict(lista_id=lista["id"], contato_id=contato, adicionado_em=stamp)
            )
            lotes[EventoConsentimento].append(
                {**evento, "id": eid, "contato_id": contato, "identidade_contato_id": identidade}
            )
            lotes[PreferenciaContato].append(
                dict(
                    escopo_id=cf[4]["id"],
                    identidade_contato_id=identidade,
                    categoria="promocoes",
                    estado="autorizado",
                    evento_origem_id=eid,
                    atualizado_em=stamp,
                )
            )
            lotes[CampanhaSelecao].append(
                dict(campanha_id=cid, identidade_contato_id=identidade, selecionado_em=stamp)
            )
        for model, values in lotes.items():
            if values:
                s.execute(insert(model), values)


@pytest.mark.parametrize("total", [250, 1000, 8000])
def test_medicao_fotografia_revalidacao_e_latencia(simulavel, monkeypatch, total):
    popular(simulavel, total)
    iniciais, integrais = [], []
    admitir_original = b2.ExecutorMock.admitir

    def admissao(executor, *args, **kwargs):
        autenticar = executor.p._autenticar_fotografia

        def integral(*values):
            start = perf_counter()
            result = autenticar(*values)
            integrais.append(perf_counter() - start)
            return result

        start = perf_counter()
        with monkeypatch.context() as patch:
            patch.setattr(executor.p, "_autenticar_fotografia", integral)
            result = admitir_original(executor, *args, **kwargs)
        iniciais.append(perf_counter() - start)
        return result

    monkeypatch.setattr(b2.ExecutorMock, "admitir", admissao)
    f, receipt, ctx, executor, adm = b2.construir(simulavel)
    # Tres admissoes explicitamente refeitas na mesma fotografia, sem despachos.
    for _ in range(2):
        adm = b2.admitir(f, executor, receipt)
    fotos, atuais, passos = [], [], []
    acessar, avaliar = executor._acessar_item, executor.p._atuais

    def fotografia(*args):
        start = perf_counter()
        result = acessar(*args)
        fotos.append(perf_counter() - start)
        return result

    def revalidar(*args):
        start = perf_counter()
        result = avaliar(*args)
        atuais.append(perf_counter() - start)
        return result

    monkeypatch.setattr(executor, "_acessar_item", fotografia)
    monkeypatch.setattr(
        executor.p, "_autenticar_fotografia", lambda *_: pytest.fail("Varredura integral por passo")
    )
    monkeypatch.setattr(executor.p, "_atuais", revalidar)
    esperada = [
        i["id"] for i in sorted(b2.linhas(f, "campanha_execucao_itens"), key=lambda i: i["ordem"])
    ][:3]
    consultas, por_passo = [], []

    def observar(_conn, _cursor, sql, *_):
        consultas.append(sql)

    event.listen(f["db"].engine, "before_cursor_execute", observar)
    try:
        for _ in range(3):
            antes = len(consultas)
            start = perf_counter()
            assert executor.passo(adm)["estado"] == "aceita_provedor"
            passos.append(perf_counter() - start)
            por_passo.append(len(consultas) - antes)
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", observar)
    assert len(set(por_passo)) == 1
    fotos_sql = [sql for sql in consultas if "FROM campanha_preparacao_itens" in sql]
    assert len(fotos_sql) == 3 and all("identidade_contato_id = ?" in sql for sql in fotos_sql)
    tentativas = b2.linhas(f, "tentativas_envio")
    assert [t["item_id"] for t in sorted(tentativas, key=lambda t: t["reservada_em"])] == esperada
    # Concorrencia controlada: pausa e opt-out esperam uma chamada ja admitida.
    # Nao inclui a autenticacao na latencia; esse custo e medido acima.
    entrou, continuar, pause_wait, opt_wait = (Event() for _ in range(4))
    original = executor._invocar
    lock = f["svc"].repo.lock
    ids = {}

    class Observado:
        def __enter__(self):
            actor = ids.get(get_ident())
            if actor:
                actor.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    def esperar(*args):
        entrou.set()
        assert continuar.wait(10)
        return original(*args)

    monkeypatch.setattr(executor, "_invocar", esperar)
    monkeypatch.setattr(f["svc"].repo, "lock", Observado())
    _, _, svc, contato, escopo, identidade, token = f["r"][-1]
    versao = svc.estado(contato["id"], escopo["id"])["versao"]
    data = b2.b1.anterior.consent.entrada(escopo, identidade, versao, "revogou")

    def pausar():
        ids[get_ident()] = pause_wait
        start = perf_counter()
        b2.b1.pausa(f, receipt)
        return perf_counter() - start

    def optout():
        ids[get_ident()] = opt_wait
        start = perf_counter()
        svc.registrar(contato["id"], data, token)
        return perf_counter() - start

    with ThreadPoolExecutor(3) as pool:
        pending = pool.submit(executor.passo, adm)
        assert entrou.wait(10)
        pause = pool.submit(pausar)
        opt = pool.submit(optout)
        try:
            assert pause_wait.wait(10) and opt_wait.wait(10)
        finally:
            continuar.set()
        assert pending.result(timeout=30)["estado"] == "aceita_provedor"
        latencia_pausa, latencia_opt = pause.result(timeout=30), opt.result(timeout=30)
    assert b2.estado(f, receipt) == "pausada"
    with f["db"].engine.begin() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    print(
        "BENCH_B21 "
        + json.dumps(
            dict(
                destinatarios=total,
                amostras=3,
                consultas_por_passo=por_passo,
                autenticacao_inicial_s=mean(integrais),
                admissao_inicial_s=mean(iniciais),
                acesso_item_s=mean(fotos[:3]),
                revalidacao_s=mean(atuais[:3]),
                passo_s=mean(passos),
                estimativa_8000_s=mean(iniciais) + mean(passos) * 8000,
                pausa_s=latencia_pausa,
                optout_s=latencia_opt,
            ),
            sort_keys=True,
        )
    )
