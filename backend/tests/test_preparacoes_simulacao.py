"""Fotografias mock reais; nenhuma execucao ou I/O de provedores."""

import hashlib
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, get_ident
from uuid import uuid4

import pytest
import test_consentimentos as consent
import test_contas_envio as contas
import test_preparacoes as prep
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from app.campanhas import preparacoes as mod
from app.campanhas.configuracao_simulacao import resumo_configuracao
from app.campanhas.models import ContextoCampanhas, Preparacao
from app.campanhas.schemas import RascunhoEdicao
from app.contas_envio.schemas import ContaEdicao, ContaEntrada
from app.provedores.mock import MockProvedor

cadastro = prep.cadastro
consentimento = prep.consentimento
rascunho = prep.rascunho
pronta = prep.pronta


@pytest.fixture
def simulavel(pronta):
    r, identifier, _ = pronta
    c = r[0]
    account = contas.criar(c, plataforma="whatsapp", provedor="mock")
    campaign = c.get("/campanhas/" + identifier).json()
    result = c.patch(
        "/campanhas/" + identifier,
        json=prep.op(versao=campaign["versao"], conta_envio_id=account["id"]),
    )
    assert result.status_code == 200, result.text
    return pronta


def revisar(f, cenario="aceita_provedor", **params):
    r, identifier, _ = f
    response = r[0].get(
        f"/campanhas/{identifier}/revisao-preparacao", params={"cenario": cenario, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def entrada(f, cenario="aceita_provedor"):
    review = revisar(f, cenario)
    return prep.op(
        versao=review["versao"],
        marcador=review["marcador"],
        configuracao_simulacao=review["configuracao_simulacao"],
    )


def preparar(f, body=None):
    body = entrada(f) if body is None else body
    response = f[0][0].post(f"/campanhas/{f[1]}/preparacoes", json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def fotografia(f, receipt):
    response = f[0][0].get(f"/campanhas/{f[1]}/preparacoes/{receipt['preparacao_id']}")
    assert response.status_code == 200, response.text
    return response.json()


def confirmar(f, receipt):
    photo = fotografia(f, receipt)
    response = f[0][0].post(
        f"/campanhas/{f[1]}/preparacoes/{receipt['preparacao_id']}/confirmacao",
        json=prep.op(
            versao=receipt["versao"],
            preparacao_id=receipt["preparacao_id"],
            impressao=photo["impressao"],
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "cenario", ["aceita_provedor", "falhou", "desconhecida", "interrupcao", "excecao"]
)
def test_ciclo_sem_execucao_privacidade_e_revisao_sem_persistir(
    simulavel, monkeypatch, caplog, cenario
):
    r, identifier, code = simulavel
    c, app, _, contact, identity, *_ = r

    def forbidden(*_, **__):
        pytest.fail("Operacao acessou provedor, rede, DNS, subprocesso ou credenciais.")

    monkeypatch.setattr(MockProvedor, "executar", forbidden)
    monkeypatch.setattr(MockProvedor, "__init__", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in ("assinatura", "gravar", "recuperar", "limpar", "pendente"):
        monkeypatch.setattr(app.state.contas_envio.segredos, name, forbidden)
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        before = conn.exec_driver_sql("SELECT count(*) FROM auditoria").scalar_one()
    review = revisar(simulavel, cenario)
    assert (
        revisar(simulavel, cenario, pagina=2, limite=1, marcador=review["marcador"])[
            "configuracao_simulacao"
        ]
        == review["configuracao_simulacao"]
    )
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM auditoria").scalar_one() == before
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 0
    receipt, body = preparar(simulavel, entrada(simulavel, cenario))
    cfg = fotografia(simulavel, receipt)["referencias_congeladas"]["configuracao_simulacao"]
    assert cfg == review["configuracao_simulacao"]
    assert cfg["cenario"] == cenario
    assert c.post(f"/campanhas/{identifier}/preparacoes", json=body).json() == receipt
    confirmed = confirmar(simulavel, receipt)
    assert fotografia(simulavel, receipt)["referencias_congeladas"]["configuracao_simulacao"] == cfg
    history = c.get(f"/campanhas/{identifier}/preparacoes", params={"limite": 1}).json()
    assert history["itens"][0]["configuracao_simulacao"] == cfg
    text = c.get(
        f"/campanhas/{identifier}/preparacoes/{receipt['preparacao_id']}/itens/{identity['id']}/conteudo"
    ).json()["conteudo_renderizado"]
    assert (
        c.post(
            f"/campanhas/{identifier}/retorno-rascunho",
            json=prep.op(versao=confirmed["versao"], preparacao_id=receipt["preparacao_id"]),
        ).status_code
        == 200
    )
    assert c.post(f"/campanhas/{identifier}/preparacoes", json=body).json() == receipt
    assert fotografia(simulavel, receipt)["invalidado_em"]
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 1
        audit = "\n".join(conn.exec_driver_sql("SELECT resumo_json FROM auditoria").scalars())
        tables = (
            conn.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type='table'")
            .scalars()
            .all()
        )
        assert not {"tentativas", "mensagens", "fila"}.intersection(tables)
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM auditoria WHERE id=?", (body["operacao_id"],)
            ).scalar_one()
            == 1
        )
    for sensitive in (text, contact["nome"], code):
        assert sensitive not in audit and sensitive not in caplog.text


def test_revisao_explicita_e_mudanca_de_cenario(simulavel):
    c = simulavel[0][0]
    root = f"/campanhas/{simulavel[1]}"
    assert c.get(root + "/revisao-preparacao").status_code == 422
    assert c.get(root + "/revisao-preparacao", params={"cenario": "invalido"}).status_code == 422
    body = entrada(simulavel)
    other = revisar(simulavel, "falhou")
    changed = {**body, "configuracao_simulacao": other["configuracao_simulacao"]}
    assert c.post(root + "/preparacoes", json=changed).status_code == 409
    receipt, _ = preparar(simulavel, body)
    assert c.post(root + "/preparacoes", json=changed).status_code == 409
    changed = deepcopy(body)
    changed["configuracao_simulacao"]["conta_envio_id"] = str(uuid4())
    value = changed["configuracao_simulacao"]
    value["resumo_configuracao"] = resumo_configuracao(
        {k: v for k, v in value.items() if k != "resumo_configuracao"}
    )
    assert c.post(root + "/preparacoes", json=changed).status_code == 409
    assert (
        c.patch(
            root, json=prep.op(versao=receipt["versao"], configuracao_simulacao=value)
        ).status_code
        == 422
    )
    confirmed = confirmar(simulavel, receipt)
    assert (
        c.post(
            root + "/retorno-rascunho",
            json=prep.op(versao=confirmed["versao"], preparacao_id=receipt["preparacao_id"]),
        ).status_code
        == 200
    )
    next_receipt, _ = preparar(simulavel, entrada(simulavel, "falhou"))
    assert next_receipt["preparacao_id"] != receipt["preparacao_id"]
    assert (
        fotografia(simulavel, next_receipt)["referencias_congeladas"]["configuracao_simulacao"][
            "cenario"
        ]
        == "falhou"
    )


def test_conta_real_rejeita_simulacao_sem_alterar_fotografia(pronta):
    c = pronta[0][0]
    root = f"/campanhas/{pronta[1]}"
    assert c.get(root + "/revisao-preparacao", params={"cenario": "falhou"}).status_code == 422
    body = prep.entrada(c, pronta[1])
    from app.campanhas.configuracao_simulacao import criar_configuracao

    cfg = criar_configuracao(str(uuid4()), body["marcador"], "falhou", pronta[1]).model_dump()
    assert (
        c.post(root + "/preparacoes", json={**body, "configuracao_simulacao": cfg}).status_code
        == 409
    )
    assert (
        c.post(root + "/preparacoes", json={**body, "configuracao_simulacao": None}).status_code
        == 422
    )
    receipt, _ = prep.preparar(pronta)
    assert "configuracao_simulacao" not in fotografia(pronta, receipt)["referencias_congeladas"]


@pytest.mark.parametrize(
    "change",
    [
        "cenario",
        "resumo",
        "versao",
        "contrato",
        "modo",
        "provedor",
        "conta",
        "plataforma",
        "capacidades",
        "ausente",
        "incompleta",
    ],
)
@pytest.mark.parametrize("confirmed", [False, True])
def test_adulteracao_bloqueia_todas_as_consultas(simulavel, change, confirmed):
    c, app, _, _, identity, *_ = simulavel[0]
    receipt, _ = preparar(simulavel)
    if confirmed:
        confirmar(simulavel, receipt)
    pid = receipt["preparacao_id"]
    impression = fotografia(simulavel, receipt)["impressao"]
    root = f"/campanhas/{simulavel[1]}/preparacoes/{pid}"
    with app.state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
        header = json.loads(
            conn.exec_driver_sql(
                "SELECT fotografia_json FROM campanha_preparacoes WHERE id=?", (pid,)
            ).scalar_one()
        )
        cfg = header["configuracao_simulacao"]
        if change == "ausente":
            del header["configuracao_simulacao"]
        elif change == "incompleta":
            del cfg["cenario"]
        else:
            key, value = {
                "cenario": ("cenario", "falhou"),
                "resumo": ("resumo_configuracao", "f" * 64),
                "versao": ("versao_configuracao", 2),
                "contrato": ("versao_contrato_mock", "futuro"),
                "modo": ("modo", "real"),
                "provedor": ("provedor", "meta_cloud"),
                "conta": ("conta_envio_id", str(uuid4())),
                "plataforma": ("plataforma", "telegram"),
                "capacidades": ("capacidades", {**cfg["capacidades"], "entrega": True}),
            }[change]
            cfg[key] = value
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET fotografia_json=? WHERE id=?",
            (json.dumps(header), pid),
        )
    for path in (root, root + "/itens", root + f"/itens/{identity['id']}/conteudo"):
        response = c.get(path)
        assert response.status_code == 503, response.text
        assert response.json()["codigo"] == "integridade_comprometida"
        assert "configuracao_simulacao" not in response.text
    blocked = c.get(f"/campanhas/{simulavel[1]}/preparacoes").json()["itens"][0]
    assert set(blocked) == {"preparacao_id", "integridade", "bloqueada"}
    if not confirmed:
        assert (
            c.post(
                root + "/confirmacao",
                json=prep.op(versao=receipt["versao"], preparacao_id=pid, impressao=impression),
            ).status_code
            == 503
        )


def test_recuperacao_preserva_configuracao_e_blobs(simulavel):
    c, app, *_ = simulavel[0]
    receipt, _ = preparar(simulavel)
    confirmed = confirmar(simulavel, receipt)
    before = fotografia(simulavel, receipt)["referencias_congeladas"]
    with app.state.contatos.repo.transacao() as s:
        row = s.get(Preparacao, receipt["preparacao_id"])
        blobs = (row.manifesto, row.chave_cifrada, row.fotografia_json)
        s.get(ContextoCampanhas, "campanhas-v1").chave_dpapi = b"danificado"
    assert (
        c.post("/campanhas/chaves/recuperar", json=prep.op(codigo=simulavel[2])).status_code == 200
    )
    assert fotografia(simulavel, receipt)["referencias_congeladas"] == before
    with app.state.contatos.repo.transacao() as s:
        row = s.get(Preparacao, receipt["preparacao_id"])
        assert blobs == (row.manifesto, row.chave_cifrada, row.fotografia_json)
    assert confirmed["preparacao_id"] == receipt["preparacao_id"]


@pytest.mark.parametrize("same_operation", [False, True])
def test_preparacoes_concorrentes(simulavel, monkeypatch, same_operation):
    body = entrada(simulavel)
    other = body if same_operation else {**body, "operacao_id": str(uuid4())}
    barrier, original = Barrier(2), mod.previa

    def synchronized(*args):
        barrier.wait(timeout=15)
        return original(*args)

    monkeypatch.setattr(mod, "previa", synchronized)
    c = simulavel[0][0]
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda b: c.post(f"/campanhas/{simulavel[1]}/preparacoes", json=b), (body, other)
            )
        )
    assert sorted(r.status_code for r in responses) == (
        [201, 201] if same_operation else [201, 409]
    )
    if same_operation:
        assert responses[0].json() == responses[1].json()
    with simulavel[0][1].state.persistence.database.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 1
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM auditoria WHERE acao='preparar'"
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("mutation", ["revogacao", "conta_inativa", "troca_conta", "logout"])
def test_revalida_apos_espera_do_lock(simulavel, monkeypatch, mutation):
    r, identifier, _ = simulavel
    c, app, _, _, _, _, token, f = r
    body = entrada(simulavel)
    rendered, release, waiting = Event(), Event(), Event()
    original = mod.previa

    def pause(*args):
        rendered.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(mod, "previa", pause)
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
        future = pool.submit(c.post, f"/campanhas/{identifier}/preparacoes", json=body)
        assert rendered.wait(10)
        with lock:
            monkeypatch.setattr(repo, "lock", Observed())
            release.set()
            assert waiting.wait(10)
            if mutation == "revogacao":
                consent.registrar(f, "revogou")
            elif mutation == "conta_inativa":
                account = body["configuracao_simulacao"]["conta_envio_id"]
                app.state.contas_envio.editar(
                    account, ContaEdicao(**prep.op(versao=1, ativo=False)), token
                )
            elif mutation == "troca_conta":
                account = app.state.contas_envio.criar(
                    ContaEntrada(**contas.entrada(plataforma="whatsapp", provedor="mock")), token
                )
                app.state.campanhas.editar(
                    identifier,
                    RascunhoEdicao(**prep.op(versao=body["versao"], conta_envio_id=account["id"])),
                    token,
                )
            else:
                assert c.post("/auth/logout").status_code == 204
        response = future.result(timeout=15)
    assert response.status_code == (401 if mutation == "logout" else 409), response.text
    with app.state.persistence.database.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 0
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM auditoria WHERE id=?", (body["operacao_id"],)
            ).scalar_one()
            == 0
        )


def test_historico_real_sem_preenchimento_retroativo(pronta):
    r, identifier, _ = pronta
    c, app, _, _, identity, *_ = r
    root = f"/campanhas/{identifier}"
    old, _ = prep.preparar(pronta)
    confirmed = confirmar(pronta, old)
    with app.state.contatos.repo.transacao() as s:
        p = s.get(Preparacao, old["preparacao_id"])
        before = (p.fotografia_json, p.chave_cifrada, p.manifesto, p.impressao)
    assert (
        c.post(
            root + "/retorno-rascunho",
            json=prep.op(versao=confirmed["versao"], preparacao_id=old["preparacao_id"]),
        ).status_code
        == 200
    )
    account = contas.criar(c, plataforma="whatsapp", provedor="mock")
    campaign = c.get(root).json()
    assert (
        c.patch(
            root, json=prep.op(versao=campaign["versao"], conta_envio_id=account["id"])
        ).status_code
        == 200
    )
    new, _ = preparar(pronta)
    assert "configuracao_simulacao" in fotografia(pronta, new)["referencias_congeladas"]
    historical = fotografia(pronta, old)
    assert "configuracao_simulacao" not in historical["referencias_congeladas"]
    pages = [
        c.get(root + "/preparacoes", params={"pagina": page, "limite": 1}).json()["itens"][0]
        for page in (1, 2)
    ]
    assert "configuracao_simulacao" in pages[0] and "configuracao_simulacao" not in pages[1]
    assert (
        c.get(
            root + f"/preparacoes/{old['preparacao_id']}/itens/{identity['id']}/conteudo"
        ).status_code
        == 200
    )
    with app.state.contatos.repo.transacao() as s:
        p = s.get(Preparacao, old["preparacao_id"])
        assert before == (p.fotografia_json, p.chave_cifrada, p.manifesto, p.impressao)


def test_erro_sanitizado_e_rollback_atomico(simulavel, caplog):
    c, app, *_ = simulavel[0]
    root = f"/campanhas/{simulavel[1]}"
    body = entrada(simulavel)
    malicious = deepcopy(body)
    malicious["configuracao_simulacao"]["texto"] = "sentinela-privada-nao-publicavel"
    error = c.post(root + "/preparacoes", json=malicious)
    assert error.status_code == 422
    assert "sentinela-privada-nao-publicavel" not in error.text + caplog.text

    def fail(conn, cursor, sql, *_):
        if sql.startswith("INSERT INTO auditoria"):
            header = json.loads(
                conn.exec_driver_sql(
                    "SELECT fotografia_json FROM campanha_preparacoes"
                ).scalar_one()
            )
            assert header["configuracao_simulacao"] == body["configuracao_simulacao"]
            raise SQLAlchemyError("Falha injetada apos gravacao")

    db = app.state.persistence.database
    event.listen(db.engine, "after_cursor_execute", fail)
    try:
        assert c.post(root + "/preparacoes", json=body).status_code == 503
    finally:
        event.remove(db.engine, "after_cursor_execute", fail)
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 0
        assert (
            conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacao_itens").scalar_one() == 0
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM auditoria WHERE id=?", (body["operacao_id"],)
            ).scalar_one()
            == 0
        )
    preparar(simulavel, body)


@pytest.mark.parametrize("change", ["versao", "capacidades", "conta"])
def test_semantica_invalida_rejeitada_mesmo_com_aead_valido(simulavel, change):
    c, app, *_ = simulavel[0]
    receipt, _ = preparar(simulavel)
    pid = receipt["preparacao_id"]
    service = app.state.preparacoes
    with service.repo.transacao() as s:
        p = s.get(Preparacao, pid)
        old_header = json.loads(p.fotografia_json)
        master = mod.abrir(service._ctx(s))
        with mod.GCM(master) as root:
            old_key = root.decrypt(p.chave_nonce, p.chave_cifrada, mod.aad(old_header, "chave"))
        rows = [
            dict(row)
            for row in s.connection()
            .exec_driver_sql(
                "SELECT * FROM campanha_preparacao_itens WHERE preparacao_id="
                "? ORDER BY identidade_contato_id",
                (pid,),
            )
            .mappings()
        ]
    header = deepcopy(old_header)
    cfg = header["configuracao_simulacao"]
    if change == "versao":
        cfg["versao_configuracao"] = 2
    elif change == "capacidades":
        cfg["capacidades"]["entrega"] = True
    else:
        cfg["conta_envio_id"] = str(uuid4())
    cfg["resumo_configuracao"] = resumo_configuracao(
        {k: v for k, v in cfg.items() if k != "resumo_configuracao"}
    )
    header["marcador"] = (
        ".".join(header["marcador"].split(".")[:4]) + "." + cfg["resumo_configuracao"]
    )
    new_key = os.urandom(32)
    digest = hashlib.sha256(mod.canon(header))
    with mod.GCM(old_key) as old, mod.GCM(new_key) as new:
        for row in rows:
            if row["incluido"]:
                plain = old.decrypt(
                    row["nonce"],
                    row["conteudo_cifrado"],
                    mod.aad(old_header, "texto", row["identidade_contato_id"]),
                )
                row["nonce"] = os.urandom(12)
                row["conteudo_cifrado"] = new.encrypt(
                    row["nonce"], plain, mod.aad(header, "texto", row["identidade_contato_id"])
                )
            service._hash_item(digest, row)
        manifest_nonce = os.urandom(12)
        manifest = new.encrypt(manifest_nonce, digest.digest(), mod.aad(header, "manifesto"))
    key_nonce = os.urandom(12)
    with mod.GCM(master) as root:
        wrapped = root.encrypt(key_nonce, new_key, mod.aad(header, "chave"))
    # Somente banco descartavel: recria autenticacao valida sobre semantica proibida.
    with app.state.persistence.database.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER preparacoes_fixas")
        conn.exec_driver_sql("DROP TRIGGER campanha_preparacao_itens_sem_update")
        for row in rows:
            conn.exec_driver_sql(
                "UPDATE campanha_preparacao_itens SET nonce=?,conteudo_cifrad"
                "o=? WHERE preparacao_id=? AND identidade_contato_id=?",
                (row["nonce"], row["conteudo_cifrado"], pid, row["identidade_contato_id"]),
            )
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET fotografia_json=?,chave_nonc"
            "e=?,chave_cifrada=?,manifesto_nonce=?,manifesto=?,impressao="
            "? WHERE id=?",
            (
                mod.canon(header).decode("ascii"),
                key_nonce,
                wrapped,
                manifest_nonce,
                manifest,
                hashlib.sha256(manifest_nonce + manifest).hexdigest(),
                pid,
            ),
        )
    with service.repo.transacao() as s, mod.GCM(new_key) as cipher:
        service._integridade(s, s.get(Preparacao, pid), header, cipher)
    response = c.get(f"/campanhas/{simulavel[1]}/preparacoes/{pid}")
    assert response.status_code == 503
    assert response.json()["codigo"] == "integridade_comprometida"


@pytest.mark.parametrize(
    "tipo,motivo",
    [
        ("revogou", "categoria_revogada"),
        ("bloqueio_global", "bloqueio_global"),
        ("bloqueio_canal", "bloqueio_canal"),
    ],
)
def test_mock_respeita_supressao_sem_alterar_consentimentos(simulavel, tipo, motivo):
    c, app, _, _, _, _, _, consent_fixture = simulavel[0]
    if tipo == "bloqueio_global":
        consent.registrar(consent_fixture, tipo, None, identity=False)
    elif tipo == "bloqueio_canal":
        consent.registrar(consent_fixture, tipo, None)
    else:
        consent.registrar(consent_fixture, tipo)
    with app.state.persistence.database.engine.begin() as conn:
        before = conn.exec_driver_sql("SELECT count(*) FROM eventos_consentimento").scalar_one()
    review = revisar(simulavel)
    assert review["incluidos"] == 0 and motivo in review["itens"][0]["motivos"]
    body = prep.op(
        versao=review["versao"],
        marcador=review["marcador"],
        configuracao_simulacao=review["configuracao_simulacao"],
    )
    response = c.post(f"/campanhas/{simulavel[1]}/preparacoes", json=body)
    assert response.status_code == 409 and response.json()["codigo"] == "sem_destinatarios"
    with app.state.persistence.database.engine.begin() as conn:
        assert (
            conn.exec_driver_sql("SELECT count(*) FROM eventos_consentimento").scalar_one()
            == before
        )
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar_one() == 0
