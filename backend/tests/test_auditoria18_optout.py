"""Suspensao de opt-out entre processos; todas as bases sao descartaveis."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import test_executor_mock as b2
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError

from app.contatos.errors import NegocioError

cadastro = b2.cadastro
consentimento = b2.consentimento
rascunho = b2.rascunho
pronta = b2.pronta
simulavel = b2.simulavel
confirmada = b2.confirmada
autorizada = b2.autorizada
operacional = b2.operacional


def revogacao(f):
    _, _, svc, contato, escopo, identidade, token = f["r"][-1]
    versao = svc.estado(contato["id"], escopo["id"])["versao"]
    data = b2.b1.anterior.consent.entrada(escopo, identidade, versao, "revogou")
    return svc, contato["id"], data, token


def copiar_base(f, root, *, incluir_diario=True):
    data = root / "data"
    data.mkdir(parents=True)
    source = f["app"].state.persistence.settings.database_path
    with (
        closing(sqlite3.connect(source)) as old,
        closing(sqlite3.connect(data / source.name)) as new,
    ):
        old.backup(new)
    journal = source.with_suffix(".optouts")
    if incluir_diario and journal.exists():
        shutil.copytree(journal, (data / source.name).with_suffix(".optouts"))
    return root


def novo_processo(root, module, *args):
    result = subprocess.run(
        [sys.executable, "-B", "-m", module, *map(str, args)],
        env={
            **os.environ,
            "EDISK_ENV": "test",
            "EDISK_TEST_ROOT": str(root),
            "PYTHONPATH": os.pathsep.join(str(Path(p).resolve()) for p in ("tests", "src")),
        },
        capture_output=True,
        text=True,
        timeout=35,
    )
    return result


def consultar_processo(root, f, receipt):
    result = novo_processo(root, "helpers.optout_reopen", f["cid"], receipt["execucao_id"])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def reabrir(f, receipt, root):
    return consultar_processo(copiar_base(f, root), f, receipt)


def test_falha_antes_commit_rejeita_e_fecha_abertura_sem_promessa_de_recuperacao(
    operacional, tmp_path
):
    f, receipt, ctx, _, _ = operacional
    svc, cid, data, token = revogacao(f)

    def falhar(_):
        raise OperationalError("falha ficticia", None, None)

    event.listen(f["db"].engine, "commit", falhar)
    try:
        with pytest.raises(NegocioError, match="Solicitacao nao persistida") as error:
            svc.registrar(cid, data, token)
    finally:
        event.remove(f["db"].engine, "commit", falhar)
    assert ctx.suspenso.is_set()
    assert error.value.codigo == "optout_rejeitado"
    assert f["app"].state.execution_runtime._closed.is_set()
    assert b2.linhas(f, "consentimento_suspensoes") == []
    result = reabrir(f, receipt, tmp_path / "outra-instancia")
    # A sonda revisa e confirma explicitamente a retomada. Nao houve opt-out duravel.
    assert result == {"permitida": True, "tentativas": ["aceita_provedor"]}


@pytest.mark.parametrize("phase", ["antes_intencao_sqlite", "depois_intencao", "depois_optout"])
def test_quedas_entre_fronteiras_e_recuperacao_repetida(operacional, tmp_path, phase):
    f, receipt, *_ = operacional
    _, cid, data, _ = revogacao(f)
    root = copiar_base(f, tmp_path / phase)
    pedido = tmp_path / "pedido-ficticio.json"
    pedido.write_text(
        json.dumps({"contato_id": cid, "data": data.model_dump(mode="json")}), encoding="utf-8"
    )
    child = novo_processo(root, "helpers.optout_crash", phase, pedido)
    assert child.returncode == 73, child.stderr
    path = root / "data/edisk.sqlite3"
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("SELECT count(*) FROM consentimento_suspensoes").fetchone()[0] == (
            0 if phase == "antes_intencao_sqlite" else 1
        )
        assert c.execute(
            "SELECT count(*) FROM eventos_consentimento WHERE tipo='revogou'"
        ).fetchone()[0] == (1 if phase == "depois_optout" else 0)
    if phase == "antes_intencao_sqlite":
        assert consultar_processo(root, f, receipt) == {
            "permitida": True,
            "tentativas": ["aceita_provedor"],
        }
        return  # Queda antes de qualquer escrita nao constitui intencao duravel.
    for _ in range(2):
        assert consultar_processo(root, f, receipt) == {"permitida": False, "tentativas": []}
    with closing(sqlite3.connect(path)) as c:
        confirmed = c.execute("SELECT confirmado_em FROM consentimento_suspensoes").fetchone()[0]
        assert (confirmed is not None) == (phase == "depois_optout")
        assert c.execute("SELECT count(*) FROM auditoria WHERE acao='confirmar_optout'").fetchone()[
            0
        ] == (1 if phase == "depois_optout" else 0)
        assert c.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_resolucao_somente_apos_commit_e_preserva_diario(operacional, monkeypatch, tmp_path):
    f, receipt, ctx, executor, adm = operacional
    svc, cid, data, token = revogacao(f)
    original = svc._registrar

    def falhar(*_args):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    monkeypatch.setattr(svc, "_registrar", falhar)
    with pytest.raises(NegocioError):
        svc.registrar(cid, data, token)
    guard = f["db"].suspensoes_consentimento
    assert str(data.operacao_id) in guard.pendentes
    ctx.fechar()
    ctx2, ex2 = b2.integrar(f)
    with pytest.raises(NegocioError):
        b2.admitir(f, ex2, receipt)
    with pytest.raises(NegocioError):
        executor.passo(adm)
    monkeypatch.setattr(svc, "_registrar", original)
    result = svc.registrar(cid, data, token)
    assert svc.registrar(cid, data, token) == result
    assert not guard.pendentes
    guard.recuperar()  # Reconciliacao explicita, sem reabrir o runtime fechado.
    ctx2.barreira()
    rows = b2.linhas(f, "consentimento_suspensoes")
    assert len(rows) == 1 and rows[0]["evento_id"] == result["id"]
    assert rows[0]["confirmado_em"] is not None
    assert reabrir(f, receipt, tmp_path / "resolvida") == {"permitida": False, "tentativas": []}


def test_diario_parcial_nao_e_autoridade_nem_e_apagado(operacional, tmp_path):
    from app.core.config import Settings
    from app.persistencia.lifecycle import Persistence

    f, _, *_ = operacional
    root = copiar_base(f, tmp_path / "parcial")
    directory = (root / "data/edisk.sqlite3").with_suffix(".optouts")
    directory.mkdir(exist_ok=True)
    evidence = directory / "parcial.json"
    evidence.write_bytes(b'{"id":')
    p = Persistence(Settings("test", root))
    try:
        p.start()
        assert p.ready and p.probe()
        assert not p.database.suspensoes_consentimento.pendentes
    finally:
        p.close()
    assert evidence.read_bytes() == b'{"id":'


@pytest.mark.parametrize("tipo", ["revogou", "bloqueio_canal", "bloqueio_global"])
def test_escopo_duravel_nao_bloqueia_contatos_nao_afetados(operacional, monkeypatch, tipo):
    from uuid import uuid4

    f, _, *_ = operacional
    svc, cid, data, token = revogacao(f)
    iid, scope = str(data.identidade_contato_id), str(data.escopo_id)
    data = data.model_copy(
        update={
            "tipo": tipo,
            "categoria": data.categoria if tipo == "revogou" else None,
            "identidade_contato_id": data.identidade_contato_id
            if tipo != "bloqueio_global"
            else None,
        }
    )

    def falhar(*_):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    monkeypatch.setattr(svc, "_registrar", falhar)
    with pytest.raises(NegocioError):
        svc.registrar(cid, data, token)
    guard = f["db"].suspensoes_consentimento
    guard.recuperar()  # Reconstroi somente o escopo efetivamente persistido.
    assert guard.afetado(cid, iid, scope, "promocoes")
    assert not guard.afetado(str(uuid4()), str(uuid4()), scope, "promocoes")
    assert guard.afetado(cid, iid, scope, "eventos_grupos") == (tipo != "revogou")
    assert guard.afetado(cid, str(uuid4()), scope, "promocoes") == (tipo == "bloqueio_global")
    assert guard.afetado(cid, iid, str(uuid4()), "promocoes") == (tipo != "revogou")


@pytest.mark.parametrize("failure", ["sqlite", "arquivo"])
def test_sqlite_e_autoridade_e_arquivo_indisponivel_nao_altera_resultado(
    operacional, monkeypatch, tmp_path, failure
):
    f, receipt, _, executor, adm = operacional
    svc, cid, data, token = revogacao(f)
    guard = f["db"].suspensoes_consentimento
    if failure == "sqlite":

        def negar(_conn, _cursor, sql, *_):
            if sql.startswith("INSERT INTO consentimento_suspensoes"):
                raise OperationalError("Somente o espelho esta indisponivel", None, None)

        event.listen(f["db"].engine, "before_cursor_execute", negar)
    else:
        original = Path.mkdir

        def negar_path(path, *args, **kwargs):
            if path == guard.path:
                raise OSError("Diario indisponivel")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", negar_path)
    try:
        if failure == "sqlite":
            with pytest.raises(NegocioError) as error:
                svc.registrar(cid, data, token)
            assert error.value.codigo == "optout_rejeitado"
            with pytest.raises(NegocioError):
                executor.passo(adm)
            assert not [r for r in b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"]
        else:
            assert svc.registrar(cid, data, token)["id"] == str(data.operacao_id)
            assert b2.linhas(f, "consentimento_suspensoes")[0]["confirmado_em"] is not None
        assert not b2.linhas(f, "tentativas_envio")
    finally:
        if failure == "sqlite":
            event.remove(f["db"].engine, "before_cursor_execute", negar)
    expected = {
        "permitida": failure == "sqlite",
        "tentativas": ["aceita_provedor"] if failure == "sqlite" else [],
    }
    assert reabrir(f, receipt, tmp_path / failure) == expected


@pytest.mark.parametrize("acao", ["delete", "alterar", "confirmar", "replace"])
def test_evidencia_sql_imutavel_e_confirmacao_falsa_recusada(operacional, monkeypatch, acao):
    f, _, *_ = operacional
    svc, cid, data, token = revogacao(f)

    def falhar(*_):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    monkeypatch.setattr(svc, "_registrar", falhar)
    with pytest.raises(NegocioError):
        svc.registrar(cid, data, token)
    antes = b2.linhas(f, "consentimento_suspensoes")
    sql = {
        "delete": "DELETE FROM consentimento_suspensoes",
        "alterar": "UPDATE consentimento_suspensoes SET categoria='eventos_grupos'",
        "confirmar": "UPDATE consentimento_suspensoes SET confirmado_em=criado_em,evento_id=id",
        "replace": "INSERT OR REPLACE INTO consentimento_suspensoes "
        "SELECT * FROM consentimento_suspensoes",
    }[acao]
    with pytest.raises(IntegrityError), f["db"].engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        conn.exec_driver_sql(sql)
    assert b2.linhas(f, "consentimento_suspensoes") == antes


def test_falha_no_commit_real_optout_conserva_intencao_no_snapshot_sqlite(operacional, tmp_path):
    f, receipt, _, executor, adm = operacional
    svc, cid, data, token = revogacao(f)
    before = {
        t: b2.linhas(f, t)
        for t in (
            "campanha_preparacoes",
            "campanha_preparacao_itens",
            "campanha_autorizacoes_simulacao",
        )
    }
    writing = False

    def identificar(_conn, _cursor, sql, *_):
        nonlocal writing
        if sql.startswith("INSERT INTO eventos_consentimento"):
            writing = True

    def falhar(_):
        nonlocal writing
        if writing:
            writing = False
            raise OperationalError("Commit do optout indisponivel", None, None)

    event.listen(f["db"].engine, "before_cursor_execute", identificar)
    event.listen(f["db"].engine, "commit", falhar)
    try:
        with pytest.raises(NegocioError):
            svc.registrar(cid, data, token)
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", identificar)
        event.remove(f["db"].engine, "commit", falhar)
    assert b2.linhas(f, "consentimento_suspensoes")[0]["confirmado_em"] is None
    assert not [r for r in b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"]
    for table, rows in before.items():
        assert b2.linhas(f, table) == rows
    with pytest.raises(NegocioError):
        executor.passo(adm)
    root = copiar_base(f, tmp_path / "somente-sqlite", incluir_diario=False)
    assert consultar_processo(root, f, receipt) == {"permitida": False, "tentativas": []}
    assert reabrir(f, receipt, tmp_path / "copia-integral") == {
        "permitida": False,
        "tentativas": [],
    }


def test_replay_depois_de_falha_do_arquivo_usa_mesma_intencao_sqlite(operacional, monkeypatch):
    f, _, *_ = operacional
    svc, cid, data, token = revogacao(f)
    guard = f["db"].suspensoes_consentimento
    original = Path.mkdir

    def falhar(path, *args, **kwargs):
        if path == guard.path:
            raise OSError("Falha ficticia")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", falhar)
        result = svc.registrar(cid, data, token)
    first = b2.linhas(f, "consentimento_suspensoes")[0]
    result = svc.registrar(cid, data, token)
    assert svc.registrar(cid, data, token) == result
    after = b2.linhas(f, "consentimento_suspensoes")
    assert len(after) == 1 and after[0]["criado_em"] == first["criado_em"]
    assert after[0]["confirmado_em"] is not None
    assert not guard.inseguro and not guard.pendentes


def test_falha_de_ambos_armazenamentos_recusa_mutacao_e_fecha_admissao(operacional, monkeypatch):
    f, _, _, executor, adm = operacional
    svc, cid, data, token = revogacao(f)
    guard = f["db"].suspensoes_consentimento
    original = Path.mkdir

    def negar(path, *args, **kwargs):
        if path == guard.path:
            raise OSError("Armazenamento indisponivel")
        return original(path, *args, **kwargs)

    def negar_sql(*_):
        raise OperationalError("Armazenamento indisponivel", None, None)

    monkeypatch.setattr(Path, "mkdir", negar)
    monkeypatch.setattr(guard, "_espelhar", negar_sql)
    with pytest.raises(NegocioError):
        svc.registrar(cid, data, token)
    assert f["app"].state.execution_runtime._closed.is_set()
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert b2.linhas(f, "tentativas_envio") == []
    assert not [r for r in b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"]


def test_optout_pendente_e_reabertura_nao_repetem_desconhecida(simulavel, monkeypatch, tmp_path):
    import test_executor_mock_desempenho as volume

    volume.popular(simulavel, 2)
    f, receipt, _, executor, adm = b2.construir(simulavel, "desconhecida")
    assert executor.passo(adm)["estado"] == "desconhecida"
    pending = [r for r in b2.linhas(f, "campanha_execucao_itens") if r["estado"] == "pendente"][0]
    identity = next(
        r
        for r in b2.linhas(f, "identidades_contato")
        if r["id"] == pending["identidade_contato_id"]
    )
    svc = f["app"].state.consentimentos
    scope = f["r"][-1][4]
    version = svc.estado(identity["contato_id"], scope["id"])["versao"]
    data = b2.b1.anterior.consent.entrada(scope, identity, version, "revogou")

    def falhar(*_):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    monkeypatch.setattr(svc, "_registrar", falhar)
    with pytest.raises(NegocioError):
        svc.registrar(identity["contato_id"], data, f["token"])
    assert reabrir(f, receipt, tmp_path / "incerta") == {
        "permitida": False,
        "tentativas": ["desconhecida"],
    }


def test_revogacao_sql_sem_categoria_nao_cria_escopo_indeterminado(operacional, monkeypatch):
    from uuid import uuid4

    f, _, *_ = operacional
    svc, cid, data, token = revogacao(f)

    def falhar(*_):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    monkeypatch.setattr(svc, "_registrar", falhar)
    with pytest.raises(NegocioError):
        svc.registrar(cid, data, token)
    row = b2.linhas(f, "consentimento_suspensoes")[0]
    with pytest.raises(IntegrityError), f["db"].engine.begin() as conn:
        b2.b1.persistencia.inserir(
            conn,
            "consentimento_suspensoes",
            {
                **row,
                "id": str(uuid4()),
                "categoria": None,
            },
        )
    assert len(b2.linhas(f, "consentimento_suspensoes")) == 1


def test_sqlite_query_only_real_preserva_diario_e_bloqueia_novo_processo(operacional, tmp_path):
    f, receipt, *_ = operacional
    svc, cid, data, token = revogacao(f)

    def somente_leitura(raw, _):
        raw.autocommit = True
        raw.execute("PRAGMA query_only=ON").close()
        raw.autocommit = False

    event.listen(f["db"].engine, "connect", somente_leitura)
    try:
        with pytest.raises(NegocioError) as error:
            svc.registrar(cid, data, token)
        assert error.value.status == 503
        assert "SELECT" not in str(error.value) and "INSERT" not in str(error.value)
        assert error.value.codigo == "optout_rejeitado"
        assert not list(f["db"].suspensoes_consentimento.path.glob("*.json"))
    finally:
        event.remove(f["db"].engine, "connect", somente_leitura)
    assert f["app"].state.execution_runtime._closed.is_set()
    assert b2.linhas(f, "consentimento_suspensoes") == []
    # Apos rejeicao comprovada, outra abertura consulta SQLite e so retoma por
    # revisao/confirmacao manual efetuada explicitamente pelo helper.
    assert reabrir(f, receipt, tmp_path / "sqlite-readonly") == {
        "permitida": True,
        "tentativas": ["aceita_provedor"],
    }


def test_falha_commit_da_confirmacao_nao_libera_antes_de_verificacao_duravel(operacional, tmp_path):
    f, receipt, *_ = operacional
    svc, cid, data, token = revogacao(f)
    confirming = False

    def identificar(_conn, _cursor, sql, *_):
        nonlocal confirming
        if sql.startswith("UPDATE consentimento_suspensoes"):
            confirming = True

    def falhar(_):
        nonlocal confirming
        if confirming:
            confirming = False
            raise OperationalError("Confirmacao indisponivel", None, None)

    event.listen(f["db"].engine, "before_cursor_execute", identificar)
    event.listen(f["db"].engine, "commit", falhar)
    try:
        with pytest.raises(NegocioError):
            svc.registrar(cid, data, token)
    finally:
        event.remove(f["db"].engine, "before_cursor_execute", identificar)
        event.remove(f["db"].engine, "commit", falhar)
    assert b2.linhas(f, "consentimento_suspensoes")[0]["confirmado_em"] is None
    assert [r for r in b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"]
    root = copiar_base(f, tmp_path / "confirmacao")
    assert consultar_processo(root, f, receipt) == {"permitida": False, "tentativas": []}
    with closing(sqlite3.connect(root / "data/edisk.sqlite3")) as c:
        assert (
            c.execute("SELECT confirmado_em FROM consentimento_suspensoes").fetchone()[0]
            is not None
        )
