"""Correcoes High: SQLite real e CNG/DPAPI reais, sem chamadas externas."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, get_ident
from uuid import UUID, uuid4

import pytest
import test_campanhas as campaigns
import test_consentimentos as consent
import test_preparacoes as base
import test_templates as templates
from sqlalchemy import event, func, insert, select

from app.campanhas import preparacoes as mod
from app.campanhas.models import ContextoCampanhas, Preparacao, PreparacaoItem
from app.consentimentos.schemas import IdentidadeEntrada
from app.contatos.models import Auditoria
from app.contatos.repository import agora

cadastro = base.cadastro
consentimento = base.consentimento
rascunho = base.rascunho
pronta = base.pronta
pytestmark = pytest.mark.skipif(os.name != "nt", reason="CNG/DPAPI Windows reais")
op, preparar, entrada = base.op, base.preparar, base.entrada


def test_historico_vazio_preserva_consulta_sem_inicializar_chave(rascunho, monkeypatch):
    c, app, _, _, _, _, _, _ = rascunho
    campaign = campaigns.criar(rascunho)

    def forbidden(*_):
        pytest.fail("historico vazio nao precisa abrir chave")

    monkeypatch.setattr(mod, "abrir", forbidden)
    response = c.get(f"/campanhas/{campaign['id']}/preparacoes")
    assert response.status_code == 200, response.text
    assert response.json()["itens"] == [] and response.json()["total"] == 0
    with app.state.contatos.repo.transacao() as s:
        assert s.get(ContextoCampanhas, "campanhas-v1") is None


def duas(pronta):
    r, identifier, _ = pronta
    first, _ = preparar(pronta)
    result = r[0].post(
        f"/campanhas/{identifier}/retorno-rascunho",
        json=op(versao=first["versao"], preparacao_id=first["preparacao_id"]),
    )
    assert result.status_code == 200, result.text
    second, _ = preparar(pronta)
    return first, second


def adulterar(app, pid, change, identity=None):
    # Apenas no banco descartavel: simula corrupcao fora das rotas protegidas.
    with app.state.persistence.database.engine.begin() as conn:
        if change == "referencias":
            conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
            header = json.loads(
                conn.exec_driver_sql(
                    "SELECT fotografia_json FROM campanha_preparacoes WHERE id=?", (pid,)
                ).scalar_one()
            )
            header["escopo_id"] = str(uuid4())
            conn.exec_driver_sql(
                "UPDATE campanha_preparacoes SET fotografia_json=? WHERE id=?",
                (json.dumps(header), pid),
            )
        elif change == "manifesto":
            conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
            cipher = conn.exec_driver_sql(
                "SELECT manifesto FROM campanha_preparacoes WHERE id=?", (pid,)
            ).scalar_one()
            conn.exec_driver_sql(
                "UPDATE campanha_preparacoes SET manifesto=? WHERE id=?",
                (cipher[:-1] + bytes([cipher[-1] ^ 1]), pid),
            )
        else:
            action = "delete" if change == "remocao" else "update"
            conn.exec_driver_sql("DROP TRIGGER campanha_preparacao_itens_sem_" + action)
            if change == "remocao":
                conn.exec_driver_sql(
                    "DELETE FROM campanha_preparacao_itens WHERE preparacao_id=? "
                    "AND identidade_contato_id=?",
                    (pid, identity),
                )
            else:
                conn.exec_driver_sql(
                    "UPDATE campanha_preparacao_itens SET motivos_json=? WHERE preparacao_id=? "
                    "AND identidade_contato_id=?",
                    ('["bloqueio_global"]', pid, identity),
                )


def adicionar_excluida(pronta):
    r, identifier, _ = pronta
    c, app, _, _, _, lista, token, _ = r
    contact = c.post(
        "/contatos", json={"telefone": "+442083661177", "nome": "Contato sintetico"}
    ).json()
    ident = app.state.consentimentos.criar_identidade(
        contact["id"],
        IdentidadeEntrada(
            operacao_id=uuid4(),
            telefone_id=UUID(contact["telefones"][0]["id"]),
            canal="whatsapp",
            versao=0,
        ),
        token,
    )
    assert c.put(f"/listas/{lista['id']}/contatos/{contact['id']}").status_code == 204
    current = c.get("/campanhas/" + identifier).json()
    result = campaigns.selecionar(c, current, ident)
    assert result.status_code == 200, result.text
    return ident["id"]


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("change", ["referencias", "motivos", "remocao"])
def test_consultas_bloqueiam_adulteracao_mesmo_confirmada(pronta, confirmed, change):
    r, identifier, code = pronta
    c, app, _, _, identity, *_ = r
    excluded = adicionar_excluida(pronta)
    receipt, _ = preparar(pronta)
    pid = receipt["preparacao_id"]
    root = f"/campanhas/{identifier}/preparacoes/{pid}"
    snapshot = c.get(root).json()
    assert snapshot["integridade"] == "verificada"
    if confirmed:
        assert (
            c.post(
                root + "/confirmacao",
                json=op(
                    versao=receipt["versao"], preparacao_id=pid, impressao=snapshot["impressao"]
                ),
            ).status_code
            == 200
        )
    adulterar(app, pid, change, excluded)
    for path in (root, root + "/itens", root + f"/itens/{identity['id']}/conteudo"):
        response = c.get(path)
        assert response.status_code == 503, response.text
        assert response.json()["codigo"] == "integridade_comprometida"
        assert code not in response.text and "Contato sintetico" not in response.text
    row = c.get(f"/campanhas/{identifier}/preparacoes").json()["itens"][0]
    assert row == {"preparacao_id": pid, "integridade": "comprometida", "bloqueada": True}
    assert "confirmado_em" not in row and "incluidos" not in row


def test_revisao_pos_adulterada_nao_contorna_revogacao(pronta):
    r, identifier, _ = pronta
    c, app, _, _, _, _, _, f = r
    receipt, _ = preparar(pronta)
    pid = receipt["preparacao_id"]
    root = f"/campanhas/{identifier}/preparacoes/{pid}"
    snapshot = c.get(root).json()
    consent.registrar(f, "revogou")
    with app.state.contatos.repo.transacao() as s:
        revision = app.state.contatos.repo.revisao(s)
    with app.state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET revisao_pos=? WHERE id=?", (revision, pid)
        )
    response = c.post(
        root + "/confirmacao",
        json=op(versao=receipt["versao"], preparacao_id=pid, impressao=snapshot["impressao"]),
    )
    assert response.status_code == 503, response.text
    assert response.json()["codigo"] == "integridade_comprometida"
    with app.state.contatos.repo.transacao() as s:
        assert s.get(Preparacao, pid).confirmado_em is None


def test_contexto_historico_independe_do_novo_rascunho(pronta):
    r, identifier, _ = pronta
    c, app, _, contact, identity, old_list, token, f = r
    first, _ = preparar(pronta)
    root = f"/campanhas/{identifier}"
    pid = first["preparacao_id"]
    snapshot = c.get(root + f"/preparacoes/{pid}").json()
    result = c.post(
        root + f"/preparacoes/{pid}/confirmacao",
        json=op(versao=first["versao"], preparacao_id=pid, impressao=snapshot["impressao"]),
    )
    assert result.status_code == 200
    consent.registrar(f, "revogou")
    returned = c.post(
        root + "/retorno-rascunho", json=op(versao=result.json()["versao"], preparacao_id=pid)
    )
    assert returned.status_code == 200
    new_template = templates.criar(c, categoria="atualizacoes_solicitadas")
    new_list = c.post("/listas", json={"nome": "Lista nova"}).json()
    changed = c.patch(
        root,
        json=op(
            versao=returned.json()["versao"],
            categoria="atualizacoes_solicitadas",
            template_versao_id=new_template["template_versao_id"],
            listas=[new_list["id"]],
        ),
    )
    assert changed.status_code == 200, changed.text
    assert campaigns.selecionar(c, changed.json(), identity, False).status_code == 200
    consent.registrar(f, categoria="atualizacoes_solicitadas")
    old = c.get(root + f"/preparacoes/{pid}").json()
    assert old["referencias_congeladas"]["categoria"] == "promocoes"
    assert old["referencias_congeladas"]["listas"] == [old_list["id"]]
    row = old["itens"][0]
    assert row["identidade_contato_id"] == identity["id"] and row["incluido"]
    assert row["motivos"] == []
    assert row["impedimentos_atuais"] == ["categoria_revogada"]
    consent.registrar(f, "bloqueio_global", None, identity=False)
    current = c.get(root + f"/preparacoes/{pid}").json()["itens"][0]
    assert "bloqueio_global" in current["impedimentos_atuais"]
    assert current["motivos"] == []


def test_recupera_chave_com_fotografia_corrompida_preservada(pronta):
    r, identifier, code = pronta
    c, app, _, _, identity, *_ = r
    first, second = duas(pronta)
    adulterar(app, first["preparacao_id"], "manifesto")
    with app.state.contatos.repo.transacao() as s:
        before = {
            p.id: (p.chave_cifrada, p.manifesto, p.impressao) for p in s.scalars(select(Preparacao))
        }
        s.get(ContextoCampanhas, "campanhas-v1").chave_dpapi = b"danificado"
    response = c.post("/campanhas/chaves/recuperar", json=op(codigo=code))
    assert response.status_code == 200, response.text
    assert response.json()["chave_recuperada"]
    assert response.json()["integridade_fotografias"] == "nao_verificada"
    good = f"/campanhas/{identifier}/preparacoes/{second['preparacao_id']}"
    bad = f"/campanhas/{identifier}/preparacoes/{first['preparacao_id']}"
    assert c.get(good).json()["integridade"] == "verificada"
    assert c.get(good + f"/itens/{identity['id']}/conteudo").status_code == 200
    assert c.get(bad).status_code == 503
    history = c.get(f"/campanhas/{identifier}/preparacoes").json()["itens"]
    assert {p["integridade"] for p in history} == {"verificada", "comprometida"}
    with app.state.contatos.repo.transacao() as s:
        assert before == {
            p.id: (p.chave_cifrada, p.manifesto, p.impressao) for p in s.scalars(select(Preparacao))
        }


@pytest.mark.parametrize("failure", ["auditoria", "sessao"])
def test_recuperacao_rollback_sem_trocar_material(pronta, monkeypatch, failure):
    from datetime import timedelta

    r, _, code = pronta
    c, app, *_ = r
    preparar(pronta)
    with app.state.contatos.repo.transacao() as s:
        s.get(ContextoCampanhas, "campanhas-v1").chave_dpapi = b"danificado"
        before = app.state.contatos.repo.revisao(s)

    def fail(*_):
        raise RuntimeError("falha apos escrita")

    if failure == "auditoria":
        event.listen(Auditoria, "after_insert", fail)
    else:
        original, clock = mod.proteger, app.state.auth.clock

        def expire(*args):
            result = original(*args)
            monkeypatch.setattr(app.state.auth, "clock", lambda: clock() + timedelta(hours=9))
            return result

        monkeypatch.setattr(mod, "proteger", expire)
    try:
        response = c.post("/campanhas/chaves/recuperar", json=op(codigo=code))
    finally:
        if failure == "auditoria":
            event.remove(Auditoria, "after_insert", fail)
    assert response.status_code == (500 if failure == "auditoria" else 401), response.text
    with app.state.contatos.repo.transacao() as s:
        assert s.get(ContextoCampanhas, "campanhas-v1").chave_dpapi == b"danificado"
        assert app.state.contatos.repo.revisao(s) == before


@pytest.mark.parametrize("size", [0, 1000])
def test_recuperacao_nao_varre_historico(pronta, monkeypatch, size, capsys):
    r, _, code = pronta
    c, app, *_ = r
    first, _ = preparar(pronta)
    with app.state.contatos.repo.transacao() as s:
        original = s.get(Preparacao, first["preparacao_id"])
        rows = []
        for _ in range(size):
            row = {col.name: getattr(original, col.name) for col in Preparacao.__table__.columns}
            row.update(id=str(uuid4()), invalidado_em=agora(), chave_nonce=uuid4().bytes[:12])
            rows.append(row)
        if rows:
            s.execute(insert(Preparacao), rows)
        s.get(ContextoCampanhas, "campanhas-v1").chave_dpapi = b"danificado"

    def forbidden(*_):
        pytest.fail("recuperacao nao deve varrer fotografias")

    monkeypatch.setattr(app.state.preparacoes, "_integridade", forbidden)
    statements = []

    def observe(_c, _cur, sql, *_):
        statements.append(sql.lower())

    engine = app.state.persistence.database.engine
    event.listen(engine, "after_cursor_execute", observe)
    start = time.perf_counter()
    try:
        response = c.post("/campanhas/chaves/recuperar", json=op(codigo=code))
    finally:
        event.remove(engine, "after_cursor_execute", observe)
    seconds = time.perf_counter() - start
    assert response.status_code == 200, response.text
    assert not any(
        "campanha_preparacoes" in sql or "campanha_preparacao_itens" in sql for sql in statements
    )
    assert response.json()["integridade_fotografias"] == "nao_verificada"
    with capsys.disabled():
        print(f"RECUPERACAO14 historico={size + 1} consultas_fotografia=0 segundos={seconds:.4f}")


def test_preparacoes_com_operacoes_distintas(pronta, monkeypatch):
    r, identifier, _ = pronta
    c, app, *_ = r
    body = entrada(c, identifier)
    bodies = [op(versao=body["versao"], marcador=body["marcador"]) for _ in range(2)]
    barrier, original = Barrier(2), mod.previa

    def synchronized(*args):
        barrier.wait(timeout=15)
        return original(*args)

    monkeypatch.setattr(mod, "previa", synchronized)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda b: c.post(f"/campanhas/{identifier}/preparacoes", json=b), bodies)
        )
    assert sorted(p.status_code for p in results) == [201, 409]
    winner = next(i for i, p in enumerate(results) if p.status_code == 201)
    monkeypatch.setattr(mod, "previa", original)
    assert (
        c.post(f"/campanhas/{identifier}/preparacoes", json=bodies[winner]).json()
        == results[winner].json()
    )
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Preparacao)) == 1
        assert s.scalar(select(func.count()).select_from(PreparacaoItem)) == 1
        assert s.get(Auditoria, bodies[1 - winner]["operacao_id"]) is None
        assert (
            s.scalar(
                select(func.count()).select_from(Auditoria).where(Auditoria.acao == "preparar")
            )
            == 1
        )


def test_confirmacoes_concorrentes(pronta):
    r, identifier, _ = pronta
    c, app, *_ = r
    receipt, _ = preparar(pronta)
    root = f"/campanhas/{identifier}/preparacoes/{receipt['preparacao_id']}"
    photo = c.get(root).json()
    bodies = [
        op(
            versao=receipt["versao"],
            preparacao_id=receipt["preparacao_id"],
            impressao=photo["impressao"],
        )
        for _ in range(2)
    ]
    barrier = Barrier(2)

    def confirm(body):
        barrier.wait(timeout=15)
        return c.post(root + "/confirmacao", json=body)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(confirm, bodies))
    assert sorted(p.status_code for p in results) == [200, 409]
    winner = next(i for i, p in enumerate(results) if p.status_code == 200)
    assert c.post(root + "/confirmacao", json=bodies[winner]).json() == results[winner].json()
    with app.state.contatos.repo.transacao() as s:
        assert s.get(Auditoria, bodies[1 - winner]["operacao_id"]) is None
        assert (
            s.scalar(
                select(func.count())
                .select_from(Auditoria)
                .where(Auditoria.acao == "confirmar_preparacao")
            )
            == 1
        )
        assert (
            s.scalar(
                select(func.count())
                .select_from(Preparacao)
                .where(Preparacao.invalidado_em.is_(None))
            )
            == 1
        )


@pytest.mark.parametrize("action", ["preparar", "confirmar"])
@pytest.mark.parametrize("invalidation", ["logout", "expiracao"])
def test_sessao_revalidada_apos_espera(pronta, monkeypatch, action, invalidation):
    from datetime import timedelta

    r, identifier, _ = pronta
    c, app, *_ = r
    root = f"/campanhas/{identifier}"
    rendered, release, waiting = Event(), Event(), Event()
    if action == "preparar":
        body = entrada(c, identifier)
        original = mod.previa

        def pause(*args):
            rendered.set()
            assert release.wait(15)
            return original(*args)

        monkeypatch.setattr(mod, "previa", pause)
        path = root + "/preparacoes"
    else:
        receipt, _ = preparar(pronta)
        photo = c.get(root + "/preparacoes/" + receipt["preparacao_id"]).json()
        body = op(
            versao=receipt["versao"],
            preparacao_id=receipt["preparacao_id"],
            impressao=photo["impressao"],
        )
        path = root + "/preparacoes/" + receipt["preparacao_id"] + "/confirmacao"
    repo = app.state.preparacoes.repo
    lock, main = repo.lock, get_ident()

    class Observed:
        def __enter__(self):
            if get_ident() != main:
                waiting.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    with ThreadPoolExecutor(1) as pool:
        if action == "preparar":
            future = pool.submit(c.post, path, json=body)
            assert rendered.wait(10)
        with lock:
            monkeypatch.setattr(repo, "lock", Observed())
            if action == "confirmar":
                future = pool.submit(c.post, path, json=body)
            else:
                release.set()
            assert waiting.wait(10)
            if invalidation == "logout":
                assert c.post("/auth/logout").status_code == 204
            else:
                clock = app.state.auth.clock
                monkeypatch.setattr(app.state.auth, "clock", lambda: clock() + timedelta(hours=9))
        response = future.result(timeout=15)
    assert response.status_code == 401, response.text
    with repo.transacao() as s:
        assert s.get(Auditoria, body["operacao_id"]) is None
        if action == "preparar":
            assert s.scalar(select(func.count()).select_from(Preparacao)) == 0
        else:
            assert s.get(Preparacao, body["preparacao_id"]).confirmado_em is None
