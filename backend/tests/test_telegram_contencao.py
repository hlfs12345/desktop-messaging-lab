"""Contenção externa SQLite e encerramento, com HTTP controlado e dados temporários."""

from threading import Event, Thread

import test_telegram_recepcao as telegram

cadastro = telegram.cadastro
recepcao = telegram.recepcao


def test_fechar_nao_espera_lock_do_banco(recepcao):
    f = recepcao
    f.server.block_token = telegram.SECRET
    telegram.iniciar(f, [telegram.update(1)])
    assert f.server.em_rede.wait(5)
    locked, release, closed = Event(), Event(), Event()

    def segurar():
        with f.svc.repo.lock:
            locked.set()
            release.wait(5)

    def fechar():
        f.runtime.fechar()
        closed.set()

    holder = Thread(target=segurar)
    closer = Thread(target=fechar)
    holder.start()
    assert locked.wait(2)
    closer.start()
    try:
        assert closed.wait(0.5), "Fechamento ficou esperando o lock do repositório"
        assert f.runtime._stop.is_set()
    finally:
        release.set()
        f.server.release.set()
        holder.join(2)
        closer.join(2)


def test_leitor_sqlite_aberto_api_responde_e_receptor_retoma(recepcao):
    import sqlite3
    from time import monotonic

    from sqlalchemy import event

    f = recepcao
    telegram.contexto(f)
    path = f.app.state.persistence.settings.database_path
    reader = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=False)
    attempted = Event()

    def inserido(_conn, _cursor, sql, *_):
        if sql.startswith("INSERT INTO telegram_recibos"):
            attempted.set()

    engine = f.svc.repo.database.engine
    event.listen(engine, "after_cursor_execute", inserido)
    try:
        reader.execute("SELECT count(*) FROM contatos").fetchone()
        telegram.iniciar(f, [telegram.update(1), telegram.update(2, "/stop")])
        assert attempted.wait(5)
        before = monotonic()
        response = f.c.get("/contatos")
        assert response.status_code == 200, response.text
        assert monotonic() - before < 2
        assert f.c.get("/contas-envio").status_code == 200
        assert telegram.estado(f)["recibos"] == 0
        assert telegram.estado(f)["offset"] is None
        assert all(
            "offset" not in params
            for _, method, params in f.server.requests
            if method == "getUpdates"
        )
        reader.close()
        assert telegram.aguardar(f, 2) == [1, 2]
        assert telegram.estado(f)["offset"] == 3
        assert telegram.estado(f)["inscricao"] == "revogada"
    finally:
        reader.close()
        event.remove(engine, "after_cursor_execute", inserido)


def test_drenagem_tem_prazo_incluindo_lock_de_controle(recepcao):
    from time import monotonic

    f = recepcao
    locked, release = Event(), Event()

    def segurar():
        with f.runtime._controle:
            locked.set()
            release.wait(5)

    holder = Thread(target=segurar)
    holder.start()
    assert locked.wait(2)
    try:
        before = monotonic()
        assert not f.runtime.drenar(timeout=0.05)
        assert monotonic() - before < 0.5
        assert f.runtime._stop.is_set()
        assert not f.svc.repo.database.closed
    finally:
        release.set()
        holder.join(2)


def test_cancelamento_nao_espera_lock_do_cliente_http(recepcao):
    f = recepcao
    f.server.block_token = telegram.SECRET
    telegram.iniciar(f, [telegram.update(1)])
    assert f.server.em_rede.wait(5)
    closed = Event()

    def fechar():
        f.runtime.fechar()
        closed.set()

    closer = Thread(target=fechar)
    with f.runtime._client._lock:
        closer.start()
        try:
            assert closed.wait(0.5)
        finally:
            f.server.release.set()
    closer.join(2)


def test_escrita_bloqueada_retorna_503_sem_dados_parciais(recepcao):
    import sqlite3
    from contextlib import closing

    f = recepcao
    path = f.app.state.persistence.settings.database_path
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=False)) as reader:
        reader.execute("SELECT count(*) FROM contatos").fetchone()
        response = telegram.test_contatos.criar(f.c)
        assert response.status_code == 503
        assert response.json()["codigo"] == "banco_ocupado"
        assert "outra conexão" in response.json()["detail"]
        assert reader.execute("SELECT count(*) FROM contatos").fetchone()[0] == 0
        assert str(path) not in response.text


def test_fechar_com_leitor_aberto_revoga_sessao_na_reabertura(settings, authorization):
    import sqlite3
    from time import monotonic

    import pytest
    from fastapi.testclient import TestClient

    from app.auth.errors import AuthError
    from app.contatos.repository import agora, uuid
    from app.importacoes.models import Importacao
    from app.importacoes.service import Previa
    from app.main import create_app

    app = create_app(settings, authorization=authorization)
    headers = {"Authorization": f"Bearer {authorization.token}"}
    reader = None
    try:
        with TestClient(app, base_url="http://127.0.0.1", headers=headers) as c:
            data = {"nome": "Teste", "login": "teste", "senha": "frase fictícia de teste 489!"}
            assert c.post("/auth/configuracao-inicial", json=data).status_code == 201
            token = c.post("/auth/login", json={k: data[k] for k in ("login", "senha")}).json()[
                "token"
            ]
            identifier = uuid()
            usuario = app.state.auth.current(token).usuario.id
            with app.state.contatos.repo.transacao() as session:
                session.add(
                    Importacao(
                        id=identifier,
                        usuario_id=usuario,
                        nome_arquivo="teste.csv",
                        status="validando",
                        normalizacao_versao="teste",
                        criado_em=agora(),
                    )
                )
            preview = Previa(
                id=identifier, usuario=usuario, extension=".csv", payload=b"temporario"
            )
            app.state.importacoes.previa = preview
            reader = sqlite3.connect(
                settings.database_path.as_uri() + "?mode=ro", uri=True, autocommit=False
            )
            reader.execute("SELECT count(*) FROM contatos").fetchone()
            before = monotonic()
        assert monotonic() - before < 2
        assert app.state.persistence.database.closed
        assert app.state.shutdown_complete
        assert app.state.shutdown_sessions_revoked is False
        assert app.state.shutdown_imports_reconciled is False
        assert app.state.importacoes.closed and app.state.importacoes.previa is None
        assert preview.cancelada.is_set()
        assert reader.execute(
            "SELECT status FROM importacoes WHERE id=?", (identifier,)
        ).fetchone() == ("validando",)
        assert not app.state.telegram_recepcao._thread.is_alive()
        with pytest.raises(AuthError):
            app.state.auth.current(token)
    finally:
        if reader is not None:
            reader.close()
    reopened = create_app(settings, authorization=authorization)
    with TestClient(reopened, base_url="http://127.0.0.1", headers=headers):
        with pytest.raises(AuthError) as raised:
            reopened.state.auth.current(token)
        assert raised.value.status == 401
        with reopened.state.persistence.database.transaction() as session:
            assert session.get(Importacao, identifier).status == "interrompida"
