"""DPAPI real Windows e falhas transacionais; sem tráfego de provedores."""

import hashlib
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
import test_contatos
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session
from test_contas_envio import criar

from app.auth.errors import AuthError
from app.auth.models import Usuario
from app.contas_envio.models import ContaEnvio
from app.contas_envio.schemas import ContaEdicao, CredencialEntrada, RevisaoEntrada
from app.contas_envio.segredos import SegredosWindows, dpapi, indisponivel
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria
from app.core.config import Settings

cadastro = test_contatos.cadastro
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Exige DPAPI real do Windows")
TOKEN = "sentinela-ficticia-local_928374:ABcd"


def put(c, root, versao=1, **changes):
    return c.put(
        root + "/credencial",
        json={"operacao_id": str(uuid4()), "versao": versao, "credencial": TOKEN, **changes},
    )


def referencia(app, identifier):
    with app.state.contatos.repo.transacao() as s:
        return s.get(ContaEnvio, identifier).segredo_ref


def test_dpapi_real_roundtrip_reabertura_corrupcao_e_contexto(settings, tmp_path):
    raw = TOKEN.encode()
    encrypted = dpapi(raw)
    assert raw not in encrypted
    assert dpapi(encrypted, decrypt=True) == raw
    with pytest.raises(NegocioError):
        dpapi(b"ciphertext danificado", decrypt=True)
    store = SegredosWindows(settings)
    identifier = str(uuid4())
    generation = store.gravar(identifier, "meta_cloud", TOKEN)
    reopened = SegredosWindows(settings)
    assert reopened.recuperar(identifier, "meta_cloud", generation) == TOKEN
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; from pathlib import Path; from app.core.config import Settings; "
            "from app.contas_envio.segredos import SegredosWindows; "
            "store=SegredosWindows(Settings('test',Path(sys.argv[1]))); "
            "assert store.recuperar(sys.argv[2], 'meta_cloud', sys.argv[3]); print('recuperado')",
            str(settings.root),
            identifier,
            generation,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert child.stdout.strip() == "recuperado"
    assert TOKEN not in child.stdout + child.stderr
    wrong_generation = uuid4().hex
    wrong_path = reopened._path(identifier, wrong_generation)
    wrong_path.write_bytes(reopened._path(identifier, generation).read_bytes())
    with pytest.raises(NegocioError):
        reopened.recuperar(identifier, "meta_cloud", wrong_generation)
    with pytest.raises(NegocioError):
        reopened.recuperar(identifier, "telegram_bot_api", generation)
    other = str(uuid4())
    target = reopened._path(other, generation)
    target.parent.mkdir()
    target.write_bytes(reopened._path(identifier, generation).read_bytes())
    with pytest.raises(NegocioError):
        reopened.recuperar(other, "meta_cloud", generation)
    # Mesmo usuário Windows: a aplicação ainda rejeita outro ambiente/raiz.
    alternate = SegredosWindows(Settings("test", tmp_path / "outro-ambiente"))
    target = alternate._path(identifier, generation)
    target.parent.mkdir(parents=True)
    target.write_bytes(reopened._path(identifier, generation).read_bytes())
    with pytest.raises(NegocioError):
        alternate.recuperar(identifier, "meta_cloud", generation)
    alternate.contexto = {**store.contexto, "ambiente": "development"}
    with pytest.raises(NegocioError):
        alternate.recuperar(identifier, "meta_cloud", generation)
    source = reopened._path(identifier, generation)
    source.write_bytes(b"corrompido")
    with pytest.raises(NegocioError):
        reopened.recuperar(identifier, "meta_cloud", generation)


def test_ciclo_segredo_idempotencia_sem_vazamento(cadastro, caplog, monkeypatch):
    def sem_rede(*_args, **_kwargs):
        raise AssertionError("Chamada externa proibida")

    monkeypatch.setattr(socket.socket, "connect", sem_rede)
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    body = {"operacao_id": str(uuid4()), "versao": 1, "credencial": TOKEN}
    saved = c.put(root + "/credencial", json=body)
    assert saved.status_code == 200, saved.text
    assert c.put(root + "/credencial", json=body).json() == saved.json()
    assert c.put(root + "/credencial", json={**body, "credencial": TOKEN + "x"}).status_code == 409
    assert c.get(root).json()["configuracao_local"] == "credencial_armazenada"
    assert c.get(root).headers["cache-control"] == "no-store"
    assert "segredo_ref" not in c.get(root).json()
    old = referencia(app, first["id"])
    assert (
        c.patch(root, json={"operacao_id": str(uuid4()), "versao": 2, "ativo": False}).status_code
        == 200
    )
    assert referencia(app, first["id"]) == old
    assert put(c, root, 3, credencial=TOKEN + "-nova").status_code == 200
    store = app.state.contas_envio.segredos
    assert not store._path(first["id"], old).exists()
    remove = {"operacao_id": str(uuid4()), "versao": 4}
    assert c.request("DELETE", root + "/credencial", json=remove).status_code == 200
    assert referencia(app, first["id"]) is None
    # Replay antigo não reinstala credencial nem depende da geração já apagada.
    assert c.put(root + "/credencial", json=body).json() == saved.json()
    assert referencia(app, first["id"]) is None
    store2 = SegredosWindows(app.state.persistence.settings)
    assert store2.assinatura(b"metadados", TOKEN) == store.assinatura(b"metadados", TOKEN)
    assert c.get(root).json()["configuracao_local"] == "pendente"
    assert TOKEN not in caplog.text
    with app.state.contatos.repo.transacao() as s:
        audit = " ".join(s.scalars(select(Auditoria.resumo_json)))
        assert TOKEN not in audit
        assert hashlib.sha256(TOKEN.encode()).hexdigest() not in audit
    for response in [saved, c.get(root), c.get("/contas-envio")]:
        assert TOKEN not in response.text
    data = app.state.persistence.settings.database_path.read_bytes()
    assert TOKEN.encode() not in data
    assert hashlib.sha256(TOKEN.encode()).hexdigest().encode() not in data
    for path in store.root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()


@pytest.mark.parametrize("when", ["protect", "publish", "before_commit", "after_commit"])
def test_substituicao_e_limpeza_recuperaveis(cadastro, monkeypatch, when):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    svc, store = app.state.contas_envio, app.state.contas_envio.segredos
    old = referencia(app, first["id"])
    with monkeypatch.context() as patch:

        def fail(*_a, **_k):
            raise indisponivel()

        if when == "protect":
            patch.setattr("app.contas_envio.segredos.dpapi", fail)
        elif when == "publish":
            patch.setattr(store, "_publish", fail)
        elif when == "before_commit":
            patch.setattr(svc, "_registrar", fail)
        else:
            patch.setattr(store, "limpar", fail)
        response = put(c, root, 2, credencial=TOKEN + "-substituida")
        assert response.status_code == (200 if when == "after_commit" else 503)
        if when == "after_commit":
            assert response.json()["limpeza_pendente"] is True
    if when != "after_commit":
        assert referencia(app, first["id"]) == old
        assert store.recuperar(first["id"], "telegram_bot_api", old) == TOKEN
    else:
        assert referencia(app, first["id"]) != old
        assert store._path(first["id"], old).exists()
    # Nova instância do serviço recupera inclusive gerações publicadas sem commit.
    from app.contas_envio.service import ContasService

    recovered = ContasService(svc.repo, svc.auth, SegredosWindows(app.state.persistence.settings))
    assert recovered.recuperar()
    assert not list(store.root.rglob("*.partial"))
    assert len(list(store.root.rglob("*.dpapi"))) == 2  # chave HMAC + geração ativa


@pytest.mark.parametrize("logical", [False, True])
def test_exclusao_primeiro_desvincula_e_depois_limpa(cadastro, monkeypatch, logical):
    c, app, token = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    svc, store = app.state.contas_envio, app.state.contas_envio.segredos
    old = referencia(app, first["id"])
    original = store.limpar

    def interrupted(_refs):
        assert referencia(app, first["id"]) is None
        raise indisponivel()

    monkeypatch.setattr(store, "limpar", interrupted)
    if logical:
        result = svc.editar(
            first["id"], ContaEdicao(operacao_id=uuid4(), versao=2, remover=True), token
        )
    else:
        result = svc.credencial(
            first["id"], RevisaoEntrada(operacao_id=uuid4(), versao=2), token, excluir=True
        )
    assert result["limpeza_pendente"]
    assert store._path(first["id"], old).exists()
    monkeypatch.setattr(store, "limpar", original)
    assert svc.recuperar()
    assert not store._path(first["id"], old).exists()


def test_corrupcao_e_reconfiguracao(cadastro):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    store = app.state.contas_envio.segredos
    path = store._path(first["id"], referencia(app, first["id"]))
    path.write_bytes(b"quebrado")
    assert c.get(root).json()["configuracao_local"] == "armazenamento_indisponivel"
    assert put(c, root, 2).status_code == 200
    assert c.get(root).json()["configuracao_local"] == "credencial_armazenada"


def test_limpeza_nao_apaga_arquivos_externos(settings, tmp_path):
    store = SegredosWindows(settings)
    account = str(uuid4())
    generation = store.gravar(account, "meta_cloud", TOKEN)
    outsider = tmp_path / "arquivo-externo.txt"
    outsider.write_text("preservar", encoding="utf-8")
    # Hardlink é recusado antes de ler/apagar o alvo, sem exigir Developer Mode.
    link = store._path(account, uuid4().hex)
    os.link(outsider, link)
    with pytest.raises(NegocioError):
        store.limpar(set())
    assert outsider.read_text(encoding="utf-8") == "preservar"
    link.unlink()
    unknown = store.root / "arquivo-desconhecido.txt"
    unknown.write_text("preservar", encoding="utf-8")
    store.limpar(set())
    assert unknown.exists()
    assert not store._path(account, generation).exists()
    with pytest.raises(NegocioError):
        store.recuperar("../externo", "meta_cloud", generation)


def test_qr_recusa_credenciais_e_limites_sem_eco(cadastro):
    c, app, _ = cadastro
    first = criar(c, plataforma="whatsapp", provedor="qr_candidato")
    root = "/contas-envio/" + first["id"]

    class WhatsappFalso:
        sessao = False
        desvinculos = 0
        suspensoes = 0

        def status(self, _):
            return {
                "estado": "desconectado" if self.sessao else "pareamento_necessario",
                "sessao_persistida": self.sessao,
                "qr": None,
            }

        def conectar(self, _):
            return {
                **self.status(None),
                "estado": "reconectando" if self.sessao else "conectando",
            }

        def desconectar(self, _):
            return self.status(None)

        def desvincular(self, _):
            self.desvinculos += 1
            self.sessao = False
            return self.status(None)

        def suspender(self, _):
            self.suspensoes += 1

    whatsapp = WhatsappFalso()
    app.state.contas_envio.whatsapp = whatsapp
    assert c.get(root).json()["configuracao_local"] == "aguarda_integracao"
    assert c.get(root + "/whatsapp").json()["estado"] == "pareamento_necessario"
    assert c.post(root + "/whatsapp/reconectar").status_code == 409
    assert c.post(root + "/whatsapp/parear").json()["estado"] == "conectando"
    whatsapp.sessao = True
    assert c.post(root + "/whatsapp/parear").status_code == 409
    assert c.post(root + "/whatsapp/reconectar").json()["estado"] == "reconectando"
    assert c.post(root + "/whatsapp/desconectar").json()["sessao_persistida"] is True
    assert (
        c.post(
            root + "/whatsapp/desvincular",
            json={"operacao_id": str(uuid4()), "versao": 1},
        ).json()["estado"]
        == "pareamento_necessario"
    )
    assert whatsapp.desvinculos == 1
    managed = criar(c, plataforma="whatsapp", provedor="qr_candidato")
    managed_root = "/contas-envio/" + managed["id"]
    whatsapp.sessao = True
    assert c.patch(
        managed_root,
        json={"operacao_id": str(uuid4()), "versao": 1, "ativo": False},
    ).status_code == 200
    assert whatsapp.sessao and whatsapp.suspensoes == 1
    assert c.patch(
        managed_root,
        json={"operacao_id": str(uuid4()), "versao": 2, "ativo": True},
    ).status_code == 200
    removed = c.patch(
        managed_root,
        json={"operacao_id": str(uuid4()), "versao": 3, "remover": True},
    )
    assert removed.status_code == 200
    assert removed.json()["whatsapp_limpeza_pendente"] is False
    assert not whatsapp.sessao and whatsapp.desvinculos == 2
    assert c.get(managed_root).json()["estado_cadastral"] == "removida"
    assert put(c, root).status_code == 422
    assert not app.state.contas_envio.segredos.root.exists() or not list(
        app.state.contas_envio.segredos.root.glob("*/*.dpapi")
    )
    for secret in ["", TOKEN + " ", "x" * 4097, TOKEN + "\n"]:
        result = put(c, root, credencial=secret)
        assert result.status_code == 422
        assert TOKEN not in result.text
    response = c.put(root + "/credencial", content="x" * 32769)
    assert response.status_code == 413
    c.headers.pop("X-eDisk-Session")
    assert c.put(root + "/credencial", content="x" * 32769).status_code == 401


@pytest.mark.parametrize("invalidation", ["logout", "expiracao", "operador"])
@pytest.mark.parametrize("mutation", ["editar", "salvar", "excluir"])
def test_revalida_sessao_e_perfil_apos_lock(cadastro, monkeypatch, invalidation, mutation):
    c, app, token = cadastro
    first = criar(c)
    repo, svc = app.state.contatos.repo, app.state.contas_envio
    lock, waiting = repo.lock, Event()

    class ObservedLock:
        def __enter__(self):
            waiting.set()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    monkeypatch.setattr(repo, "lock", ObservedLock())

    def change():
        if mutation == "editar":
            return svc.editar(
                first["id"], ContaEdicao(operacao_id=uuid4(), versao=1, ativo=False), token
            )
        if mutation == "excluir":
            return svc.credencial(
                first["id"], RevisaoEntrada(operacao_id=uuid4(), versao=1), token, excluir=True
            )
        return svc.credencial(
            first["id"], CredencialEntrada(operacao_id=uuid4(), versao=1, credencial=TOKEN), token
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(change)
            assert waiting.wait(10)
            if invalidation == "logout":
                assert c.post("/auth/logout").status_code == 204
            elif invalidation == "expiracao":
                monkeypatch.setattr(
                    app.state.auth, "clock", lambda: datetime.now(UTC) + timedelta(hours=9)
                )
            else:
                with app.state.persistence.database.transaction() as s:
                    s.execute(update(Usuario).values(perfil="operador"))
        with pytest.raises((NegocioError, AuthError)) as error:
            future.result(10)
        assert getattr(error.value, "status", None) == (403 if invalidation == "operador" else 401)
    assert not svc.segredos.root.exists()


def test_credencial_concorrente_mesma_operacao(cadastro):
    c, app, token = cadastro
    first = criar(c)
    svc = app.state.contas_envio
    data = CredencialEntrada(operacao_id=uuid4(), versao=1, credencial=TOKEN)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: svc.credencial(first["id"], data, token), range(4)))
    assert all(r == results[0] for r in results)
    assert len(list(svc.segredos.root.rglob("*.dpapi"))) == 2
    assert c.get("/contas-envio/" + first["id"]).json()["versao"] == 2


@pytest.mark.parametrize("excluir", [False, True])
def test_falha_real_no_commit_preserva_referencia(cadastro, monkeypatch, excluir):
    c, app, token = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    svc, store = app.state.contas_envio, app.state.contas_envio.segredos
    old = referencia(app, first["id"])

    # O listener executa depois de flush/auditoria, no commit SQLAlchemy real.
    def fail_commit(_session):
        raise indisponivel()

    event.listen(Session, "before_commit", fail_commit)
    try:
        data = (
            RevisaoEntrada(operacao_id=uuid4(), versao=2)
            if excluir
            else CredencialEntrada(operacao_id=uuid4(), versao=2, credencial=TOKEN + "-nova")
        )
        with pytest.raises(NegocioError):
            svc.credencial(first["id"], data, token, excluir=excluir)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert referencia(app, first["id"]) == old
    assert store.recuperar(first["id"], "telegram_bot_api", old) == TOKEN
    assert c.get(root).json()["versao"] == 2
    assert svc.recuperar()
    assert len(list(store.root.rglob("*.dpapi"))) == 2


def test_operador_e_sessao_invalida_na_api_de_credenciais(cadastro):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    with app.state.persistence.database.transaction() as s:
        s.execute(update(Usuario).values(perfil="operador"))
    assert put(c, root).status_code == 403
    assert (
        c.request(
            "DELETE", root + "/credencial", json={"operacao_id": str(uuid4()), "versao": 1}
        ).status_code
        == 403
    )
    # Negação antecede parser/limite do corpo.
    assert c.put(root + "/credencial", content="X" * 32769).status_code == 403
    assert not app.state.contas_envio.segredos.root.exists()


def test_indisponibilidade_do_diretorio_sanitizada(cadastro, monkeypatch):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]

    def denied(_path):
        raise PermissionError(TOKEN)

    monkeypatch.setattr(app.state.contas_envio.segredos, "_guard_path", denied)
    response = put(c, root)
    assert response.status_code == 503
    assert TOKEN not in response.text


@pytest.mark.parametrize("logical", [False, True])
def test_consultas_reconstroem_pendencia_sem_expor_artefatos(cadastro, monkeypatch, logical):
    from app.contas_envio.service import ContasService

    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    svc, store = app.state.contas_envio, app.state.contas_envio.segredos
    old = referencia(app, first["id"])
    original = SegredosWindows.limpar
    blocked = True

    def limpar(self, refs):
        if blocked:
            raise indisponivel()
        original(self, refs)

    monkeypatch.setattr(SegredosWindows, "limpar", limpar)
    body = {"operacao_id": str(uuid4()), "versao": 2}
    result = (
        c.patch(root, json={**body, "remover": True})
        if logical
        else c.request("DELETE", root + "/credencial", json=body)
    )
    assert result.status_code == 200 and result.json()["limpeza_pendente"]
    assert store._path(first["id"], old).exists()
    assert referencia(app, first["id"]) is None

    rebuilt = ContasService(svc.repo, svc.auth, SegredosWindows(app.state.persistence.settings))
    app.state.contas_envio = rebuilt
    assert rebuilt.recuperar() is False  # Mesmo ponto executado no lifespan.
    for response in (c.get(root), c.get("/contas-envio")):
        assert response.status_code == 200
        assert response.json()["limpeza_pendente"] is True
        assert str(store.root) not in response.text
        assert old not in response.text
        assert "segredo_ref" not in response.text
        assert TOKEN not in response.text
    state = c.get(root).json()
    assert state["estado_cadastral"] == ("removida" if logical else "ativa")
    assert state["conexao"] == "nao_verificada"
    assert store._path(first["id"], old).exists()  # GET não faz coleta.
    blocked = False
    assert rebuilt.recuperar() is True
    assert not store._path(first["id"], old).exists()
    assert c.get(root).json()["limpeza_pendente"] is False
    assert c.get("/contas-envio").json()["limpeza_pendente"] is False


def test_consulta_detecta_orfao_apos_rollback_e_falha_fechada(cadastro, monkeypatch):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    svc = app.state.contas_envio
    old = referencia(app, first["id"])
    with monkeypatch.context() as patch:

        def fail(*_):
            raise indisponivel()

        patch.setattr(svc, "_registrar", fail)
        assert put(c, root, 2, credencial=TOKEN + "-orfao").status_code == 503
    assert c.get(root).json()["limpeza_pendente"] is True
    assert referencia(app, first["id"]) == old
    assert svc.recuperar()
    assert c.get(root).json()["limpeza_pendente"] is False
    with monkeypatch.context() as patch:

        def denied(_):
            raise PermissionError("Falha de acesso fictícia")

        patch.setattr(svc.segredos, "_guard_path", denied)
        response = c.get(root)
        assert response.status_code == 200
        assert response.json()["limpeza_pendente"] is True


@pytest.mark.parametrize("oponente", ["substituir", "excluir", "remover"])
def test_operacoes_credencial_concorrentes_respeitam_revisao(cadastro, monkeypatch, oponente):
    c, app, _ = cadastro
    first = criar(c)
    root = "/contas-envio/" + first["id"]
    assert put(c, root).status_code == 200
    # Geração de outra conta deve sobreviver a toda coleta concorrente.
    other = criar(c)
    other_root = "/contas-envio/" + other["id"]
    assert put(c, other_root).status_code == 200
    store = app.state.contas_envio.segredos
    old, other_ref = referencia(app, first["id"]), referencia(app, other["id"])
    published, collected = [], []
    original_write, original_clean = store.gravar, store.limpar

    def gravar(*args):
        generation = original_write(*args)
        published.append(generation)
        return generation

    def limpar(refs):
        for account, generation in refs:
            assert store._path(account, generation).is_file()
            assert store.recuperar(account, "telegram_bot_api", generation)
        original_clean(refs)
        for account, generation in refs:
            assert store._path(account, generation).is_file()
        collected.append(frozenset(refs))

    monkeypatch.setattr(store, "gravar", gravar)
    monkeypatch.setattr(store, "limpar", limpar)
    body_a = {"operacao_id": str(uuid4()), "versao": 2, "credencial": TOKEN + "-A"}
    body_b = {"operacao_id": str(uuid4()), "versao": 2}
    method_b, path_b = ("PUT", root + "/credencial")
    if oponente == "substituir":
        body_b["credencial"] = TOKEN + "-B"
    elif oponente == "excluir":
        method_b = "DELETE"
    else:
        method_b, path_b = "PATCH", root
        body_b["remover"] = True
    calls = [("PUT", root + "/credencial", body_a), (method_b, path_b, body_b)]
    barrier = Barrier(2)

    def executar(call):
        barrier.wait(timeout=10)
        method, path, body = call
        return c.request(method, path, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(executar, calls))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(i for i, response in enumerate(results) if response.status_code == 200)
    loser = 1 - winner
    assert results[loser].json()["codigo"] == "conflito"
    assert c.get(root).json()["versao"] == 3
    current = referencia(app, first["id"])
    replacement = winner == 0 or oponente == "substituir"
    if replacement:
        assert len(published) == 1 and current == published[0] and current != old
        assert (
            store.recuperar(first["id"], "telegram_bot_api", current)
            == calls[winner][2]["credencial"]
        )
        assert c.get(root).json()["estado_cadastral"] == "ativa"
    else:
        assert not published and current is None
        assert c.get(root).json()["estado_cadastral"] == (
            "removida" if oponente == "remover" else "ativa"
        )
    assert referencia(app, other["id"]) == other_ref
    assert store.recuperar(other["id"], "telegram_bot_api", other_ref) == TOKEN
    assert not store._path(first["id"], old).exists()
    assert not list(store.root.rglob("*.partial"))
    assert len(list(store.root.glob("*/*.dpapi"))) == (2 if replacement else 1)
    assert collected
    method, path, body = calls[winner]
    assert c.request(method, path, json=body).json() == results[winner].json()
    divergent = {**body}
    if method == "PUT":
        divergent["credencial"] += "-divergente"
    else:
        divergent["versao"] = 3
    assert c.request(method, path, json=divergent).status_code == 409
    method, path, body = calls[loser]
    assert c.request(method, path, json=body).status_code == 409
    assert len(published) == (1 if replacement else 0)
    assert referencia(app, first["id"]) == current
