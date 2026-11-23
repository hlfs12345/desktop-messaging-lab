"""A2 em SQLite descartavel: sem servico operacional e sem chamar o mock."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
import test_autorizacoes_utc_migration as a1
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, event, inspect, select
from sqlalchemy.exc import IntegrityError

from app.campanhas.execucoes_models import ExecucaoCampanha, ItemExecucao
from app.persistencia.models import Base

cadastro = a1.cadastro
consentimento = a1.consentimento
rascunho = a1.rascunho
pronta = a1.pronta
simulavel = a1.simulavel
populada_0008 = a1.populada_0008
povoada = a1.povoada
vazia = a1.vazia
T0 = "2030-01-01T00:00:00.000001Z"
T1 = "2030-01-01T00:00:00.000002Z"
T2 = "2030-01-01T00:00:00.000003Z"
T3 = "2030-01-01T00:00:00.000004Z"
T4 = "2030-01-01T00:00:00.000005Z"


def inserir(conn, tabela, valores, replace=False):
    nomes = list(valores)
    conn.exec_driver_sql(
        ("INSERT OR REPLACE" if replace else "INSERT")
        + " INTO "
        + tabela
        + " ("
        + ",".join(nomes)
        + ") VALUES ("
        + ",".join("?" for _ in nomes)
        + ")",
        tuple(valores[n] for n in nomes),
    )


def criar_execucao(conn, autorizacao, **mudancas):
    valores = dict(
        id=str(uuid4()),
        campanha_id=autorizacao["campanha_id"],
        preparacao_id=autorizacao["preparacao_id"],
        autorizacao_id=autorizacao["id"],
        estado="executando",
        versao=1,
        proprietario_id=str(uuid4()),
        geracao=1,
        criado_por_usuario_id=autorizacao["autorizado_por_usuario_id"],
        alterado_por_usuario_id=autorizacao["autorizado_por_usuario_id"],
        criado_em=T0,
        atualizado_em=T0,
    )
    if "modo" in {
        row[1] for row in conn.exec_driver_sql("PRAGMA table_info(campanha_execucoes)")
    }:
        valores.update(autorizacao_telegram_id=None, modo="mock")
    valores.update(mudancas)
    inserir(conn, "campanha_execucoes", valores)
    return valores


def reservar(conn, execucao, **mudancas):
    item = conn.exec_driver_sql(
        "SELECT id FROM campanha_execucao_itens WHERE execucao_id=? ORDER BY ordem LIMIT 1",
        (execucao["id"],),
    ).scalar_one()
    valores = dict(
        id=str(uuid4()),
        execucao_id=execucao["id"],
        item_id=item,
        numero=1,
        estado="reservada",
        versao=1,
        proprietario_id=execucao["proprietario_id"],
        geracao=execucao["geracao"],
        reservada_em=T1,
        atualizado_em=T1,
        alterado_por_usuario_id=execucao["alterado_por_usuario_id"],
    )
    valores.update(mudancas)
    inserir(conn, "tentativas_envio", valores)
    return valores


def alterar(conn, tabela, identificador, versao, **campos):
    return conn.exec_driver_sql(
        "UPDATE "
        + tabela
        + " SET "
        + ",".join(n + "=?" for n in campos)
        + ",versao=versao+1 WHERE id=? AND versao=?",
        (*campos.values(), identificador, versao),
    ).rowcount


def iniciar(conn, tentativa):
    return alterar(
        conn,
        "tentativas_envio",
        tentativa["id"],
        1,
        estado="iniciada",
        invocacao_em=T2,
        atualizado_em=T2,
    )


def finalizar(conn, tentativa, estado="aceita_provedor"):
    return alterar(
        conn,
        "tentativas_envio",
        tentativa["id"],
        2,
        estado=estado,
        finalizada_em=T3,
        atualizado_em=T3,
        codigo_resultado={
            "aceita_provedor": "aceita",
            "falhou": "falha",
            "desconhecida": "sem_confirmacao",
        }[estado],
    )


@pytest.fixture
def operacional(povoada, monkeypatch):
    from app.provedores.mock import MockProvedor

    def proibido(*_, **__):
        pytest.fail("Persistencia invocou mock")

    monkeypatch.setattr(MockProvedor, "executar", proibido)
    db, settings, variante, _ = povoada
    with db.engine.begin() as conn:
        autorizacao = a1.anterior.dados(conn)
        a1.anterior.inserir(conn, autorizacao)
    a1.migrar(db, settings)
    return db, autorizacao, settings, variante


@pytest.fixture
def execucao(operacional):
    db, autorizacao, *_ = operacional
    with db.engine.begin() as conn:
        e = criar_execucao(conn, autorizacao)
    return operacional, e


def test_modelo_orm_ddl_fila_selada_e_sem_pii(execucao):
    (db, a, _, _), e = execucao
    with db.engine.begin() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        for tabela in a1.modulo().TABELAS_OPERACIONAIS:
            checks = {
                c["name"]: a1.modulo()._sql(c["sqltext"])
                for c in inspect(conn).get_check_constraints(tabela)
            }
            esperados = {
                c.name: a1.modulo()._sql(str(c.sqltext))
                for c in Base.metadata.tables[tabela].constraints
                if isinstance(c, CheckConstraint)
            }
            assert checks == esperados
        item = (
            conn.exec_driver_sql(
                "SELECT * FROM campanha_execucao_itens WHERE execucao_id=?", (e["id"],)
            )
            .mappings()
            .one()
        )
        assert item["estado"] == "pendente" and item["ordem"] == item["versao"] == 1
        for tabela in a1.modulo().TABELAS_OPERACIONAIS:
            colunas = {c["name"] for c in inspect(conn).get_columns(tabela)}
            assert not colunas & {
                "texto",
                "telefone",
                "conteudo",
                "conteudo_renderizado",
                "credencial",
                "fotografia_json",
            }
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        for identifier in conn.exec_driver_sql(
            "SELECT id FROM campanha_execucao_transicoes"
        ).scalars():
            assert str(UUID(identifier)) == identifier
    with db.transaction() as session:
        assert session.get(ExecucaoCampanha, e["id"]).preparacao_id == a["preparacao_id"]
        assert session.scalar(select(ItemExecucao)).estado == "pendente"
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        inserir(conn, "campanha_execucao_itens", {**item, "id": str(uuid4()), "ordem": 2})
    assert a1.estado(db) == antes


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("resultado", ["aceita_provedor", "falhou", "desconhecida"])
def test_ciclo_atomico_e_diario_sem_recursive_triggers(execucao, recursive, resultado):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA recursive_triggers={recursive}")
        t = reservar(conn, e)
        assert (
            conn.exec_driver_sql("SELECT estado FROM campanha_execucao_itens").scalar_one()
            == "reservado"
        )
        assert iniciar(conn, t) == 1
        assert (
            conn.exec_driver_sql("SELECT estado FROM campanha_execucao_itens").scalar_one()
            == "processando"
        )
        assert finalizar(conn, t, resultado) == 1
        final = "concluida" if resultado == "aceita_provedor" else "concluida_com_falhas"
        assert alterar(conn, "campanha_execucoes", e["id"], 1, estado=final, atualizado_em=T4) == 1
        assert (
            conn.exec_driver_sql("SELECT estado FROM campanha_execucao_itens").scalar_one()
            == resultado
        )
        journal = conn.exec_driver_sql(
            "SELECT entidade,count(*) FROM campanha_execucao_transicoes GROUP BY entidade"
        ).all()
        assert dict(journal) == {"execucao": 2, "item": 4, "tentativa": 3}
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        alterar(conn, "campanha_execucoes", e["id"], 2, estado="executando", atualizado_em=T4)
    assert a1.estado(db) == antes


@pytest.mark.parametrize(
    "tabela",
    [
        "campanha_execucoes",
        "campanha_destinatarios",
        "campanha_execucao_itens",
        "tentativas_envio",
        "campanha_execucao_transicoes",
    ],
)
@pytest.mark.parametrize("acao", ["delete", "replace", "replace_chave", "imutavel"])
def test_historico_delete_replace_e_campos_fixos(execucao, tabela, acao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        reservar(conn, e)
        conn.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        row = dict(conn.exec_driver_sql("SELECT * FROM " + tabela + " LIMIT 1").mappings().one())
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        if acao == "delete":
            conn.exec_driver_sql("DELETE FROM " + tabela + " WHERE id=?", (row["id"],))
        elif acao in {"replace", "replace_chave"}:
            inserir(
                conn,
                tabela,
                {**row, "id": str(uuid4())} if acao == "replace_chave" else row,
                replace=True,
            )
        else:
            conn.exec_driver_sql(
                "UPDATE " + tabela + " SET id=? WHERE id=?", (str(uuid4()), row["id"])
            )
    assert a1.estado(db) == antes


@pytest.mark.parametrize(
    "mudanca", ["preparacao", "autorizacao", "versao", "estado", "campanha", "proprietario"]
)
def test_origem_de_execucao_recusada(operacional, mudanca):
    db, a, *_ = operacional
    campos = {
        "preparacao": {"preparacao_id": str(uuid4())},
        "autorizacao": {"autorizacao_id": str(uuid4())},
        "versao": {"versao": 2},
        "estado": {"estado": "pausada"},
        "campanha": {"campanha_id": str(uuid4())},
        "proprietario": {"geracao": 2},
    }[mudanca]
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        criar_execucao(conn, a, **campos)
    assert a1.estado(db) == antes


@pytest.mark.parametrize(
    "mudanca", ["numero", "geracao", "item", "estado", "marcador", "resultado"]
)
def test_reserva_nao_admite_invocacao_ou_resultado(execucao, mudanca):
    (db, _, _, _), e = execucao
    campos = {
        "numero": {"numero": 2},
        "geracao": {"geracao": 2},
        "item": {"item_id": str(uuid4())},
        "estado": {"estado": "iniciada"},
        "marcador": {"invocacao_em": T1},
        "resultado": {"codigo_resultado": "aceita"},
    }[mudanca]
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        reservar(conn, e, **campos)
    assert a1.estado(db) == antes


def test_reserva_abandonada_pode_voltar_sem_reabrir_tentativa(execucao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        alterar(
            conn,
            "tentativas_envio",
            t["id"],
            1,
            estado="interrompida",
            finalizada_em=T2,
            atualizado_em=T2,
        )
        assert (
            conn.exec_driver_sql("SELECT estado FROM campanha_execucao_itens").scalar_one()
            == "pendente"
        )
        nova = reservar(conn, e, numero=2, reservada_em=T3, atualizado_em=T3)
        assert nova["id"] != t["id"]
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        alterar(conn, "tentativas_envio", t["id"], 2, estado="reservada", atualizado_em=T3)


@pytest.mark.parametrize("resultado", ["aceita_provedor", "falhou", "desconhecida"])
def test_terminal_nao_reinicia_nem_apaga_marcador(execucao, resultado):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        iniciar(conn, t)
        finalizar(conn, t, resultado)
    antes = a1.estado(db)
    for tabela, identifier, versao, campos in (
        ("tentativas_envio", t["id"], 3, {"estado": "interrompida", "invocacao_em": None}),
        ("campanha_execucao_itens", t["item_id"], 4, {"estado": "pendente"}),
    ):
        with pytest.raises(IntegrityError), db.engine.begin() as conn:
            alterar(conn, tabela, identifier, versao, atualizado_em=T4, **campos)
        assert a1.estado(db) == antes
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        reservar(conn, e, numero=2, reservada_em=T4, atualizado_em=T4)


def test_pausa_cas_e_geracao_fecham_admissao(execucao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        assert (
            alterar(conn, "campanha_execucoes", e["id"], 99, estado="pausando", atualizado_em=T2)
            == 0
        )
        novo = str(uuid4())
        alterar(
            conn,
            "campanha_execucoes",
            e["id"],
            1,
            estado="pausando",
            atualizado_em=T2,
            proprietario_id=novo,
            geracao=2,
        )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        iniciar(conn, t)
    with db.engine.begin() as conn:
        alterar(
            conn,
            "tentativas_envio",
            t["id"],
            1,
            estado="interrompida",
            finalizada_em=T3,
            atualizado_em=T3,
        )
        alterar(conn, "campanha_execucoes", e["id"], 2, estado="pausada", atualizado_em=T3)
        alterar(
            conn,
            "campanha_execucoes",
            e["id"],
            3,
            estado="executando",
            atualizado_em=T4,
            proprietario_id=str(uuid4()),
            geracao=3,
        )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        reservar(conn, e, numero=2, reservada_em=T4, atualizado_em=T4)


def test_resultado_apos_revogacao_e_retorno_preservado(execucao):
    (db, a, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        iniciar(conn, t)
        a1.anterior.revogar(conn, a, data=T2)
        finalizar(conn, t, "desconhecida")
        alterar(conn, "campanha_execucoes", e["id"], 1, estado="interrompida", atualizado_em=T4)
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?", (T4, a["preparacao_id"])
        )
    assert a1.estado(db) == antes


def test_retorno_sem_tentativas_preserva_fila_e_autorizacao(execucao):
    (db, a, _, _), e = execucao
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        a1.anterior.revogar(conn, a, data=T2)
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?", (T4, a["preparacao_id"])
        )
    with db.engine.begin() as conn:
        item = conn.exec_driver_sql("SELECT id FROM campanha_execucao_itens").scalar_one()
        alterar(
            conn,
            "campanha_execucao_itens",
            item,
            1,
            estado="interrompido",
            motivo_codigo="encerramento",
            atualizado_em=T1,
        )
        alterar(conn, "campanha_execucoes", e["id"], 1, estado="interrompida", atualizado_em=T2)
        a1.anterior.revogar(conn, a, data=T2)
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?", (T4, a["preparacao_id"])
        )
        conn.exec_driver_sql(
            "UPDATE campanhas SET status='rascunho',versao=versao+1 WHERE id=?", (a["campanha_id"],)
        )
        assert (
            conn.exec_driver_sql("SELECT estado FROM campanha_execucao_itens").scalar_one()
            == "interrompido"
        )


@pytest.mark.parametrize("etapa", ["fila", "tentativa"])
def test_falha_apos_escrita_reverte_fila_tentativa_e_diario(operacional, etapa):
    db, a, *_ = operacional
    if etapa != "fila":
        with db.engine.begin() as conn:
            e = criar_execucao(conn, a)
    antes = a1.estado(db)

    def falhar(_conn, _cursor, sql, *_):
        alvo = (
            "INSERT INTO campanha_execucoes" if etapa == "fila" else "INSERT INTO tentativas_envio"
        )
        if sql.startswith(alvo):
            # AFTER INSERT ja criou efeitos encadeados.
            assert (
                _conn.exec_driver_sql(
                    "SELECT count(*) FROM campanha_execucao_transicoes"
                ).scalar_one()
                > 0
            )
            raise RuntimeError("Falha apos escrita operacional")

    event.listen(db.engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(RuntimeError), db.engine.begin() as conn:
            if etapa == "fila":
                criar_execucao(conn, a)
            else:
                reservar(conn, e)
    finally:
        event.remove(db.engine, "after_cursor_execute", falhar)
    assert a1.estado(db) == antes


def test_downgrade_com_historico_mesmo_terminal_recusado(execucao):
    (db, _, settings, _), e = execucao
    with db.engine.begin() as conn:
        item = conn.exec_driver_sql("SELECT id FROM campanha_execucao_itens").scalar_one()
        alterar(
            conn,
            "campanha_execucao_itens",
            item,
            1,
            estado="ignorado",
            motivo_codigo="impedimento_atual",
            atualizado_em=T1,
        )
        alterar(conn, "campanha_execucoes", e["id"], 1, estado="concluida", atualizado_em=T2)
    antes = a1.estado(db)
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        a1.anterior.revision(db, a1.ORIGEM, downgrade=True)
    assert a1.estado(db) == antes
    assert a1.read_revision(settings.database_path) == a1.HEAD


def test_concorrencia_de_inicio_usa_banco_sem_lock_em_memoria(operacional):
    db, a, *_ = operacional
    barreira = Barrier(2)

    def disputar():
        barreira.wait(timeout=5)
        try:
            with db.engine.begin() as conn:
                criar_execucao(conn, a)
            return "criou"
        except IntegrityError:
            return "conflito"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: disputar(), range(2)))
    assert sorted(results) == ["conflito", "criou"]
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_execucoes").scalar_one() == 1
        assert (
            conn.exec_driver_sql("SELECT count(*) FROM campanha_execucao_itens").scalar_one() == 1
        )


@pytest.mark.parametrize(
    "coluna",
    ["execucao_id", "campanha_id", "preparacao_id", "destinatario_id", "identidade_contato_id"],
)
def test_fks_compostas_independentes_do_trigger_de_insercao(execucao, coluna):
    (db, _, _, _), _ = execucao
    with db.engine.begin() as conn:
        item = dict(conn.exec_driver_sql("SELECT * FROM campanha_execucao_itens").mappings().one())
        conn.exec_driver_sql("DROP TRIGGER campanha_execucao_itens_transicao")
        conn.exec_driver_sql("DROP TRIGGER campanha_execucao_itens_diario_update")
    # Isolar a barreira referencial, mantendo FKs ativas, em base descartavel.
    antes = a1.estado(db)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), db.engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE campanha_execucao_itens SET {coluna}=? WHERE id=?", (str(uuid4()), item["id"])
        )
    assert a1.estado(db) == antes


def test_indice_global_de_tentativas_independente_de_guardas(execucao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        for nome in (
            "tentativas_envio_origem",
            "tentativas_envio_sem_replace",
            "tentativas_envio_item_insert",
        ):
            conn.exec_driver_sql("DROP TRIGGER " + nome)
        conn.exec_driver_sql("DROP INDEX ix_tentativa_ativa_item")
    antes = a1.estado(db)
    with pytest.raises(IntegrityError, match="UNIQUE"), db.engine.begin() as conn:
        inserir(conn, "tentativas_envio", {**t, "id": str(uuid4()), "numero": 2})
    assert a1.estado(db) == antes


@pytest.mark.parametrize("etapa", ["tabela", "indice", "trigger", "versao", "commit"])
def test_falha_no_esquema_operacional_reverte_inclusive_endurecimento_a1(vazia, etapa):
    db, settings, _ = vazia
    antes = a1.estado(db)
    vistos = []
    alvos = {
        "tabela": "CREATE TABLE tentativas_envio",
        "indice": "CREATE INDEX ix_tentativas_historico",
        "trigger": "CREATE TRIGGER campanha_execucoes_origem",
        "versao": "UPDATE alembic_version",
    }

    def falhar(_conn, _cursor, sql, *_):
        if sql.lstrip().startswith(alvos[etapa]):
            vistos.append(sql)
            raise RuntimeError("Falha A2 apos DDL")

    def falhar_commit(_conn):
        vistos.append("commit")
        raise RuntimeError("Falha A2 no commit")

    evento, callback = (
        ("commit", falhar_commit) if etapa == "commit" else ("after_cursor_execute", falhar)
    )
    event.listen(db.engine, evento, callback)
    try:
        with pytest.raises(a1.MigrationError):
            a1.migrar(db, settings)
    finally:
        event.remove(db.engine, evento, callback)
    assert vistos and a1.estado(db) == antes
    a1.migrar(db, settings)
    with db.engine.begin() as conn:
        a1.modulo()._validar(conn, protegida=True)


def test_diario_falso_e_versao_sem_transicao_sao_recusados(execucao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        evento = dict(
            conn.exec_driver_sql(
                "SELECT * FROM campanha_execucao_transicoes WHERE entidade='execucao'"
            )
            .mappings()
            .one()
        )
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        inserir(
            conn,
            "campanha_execucao_transicoes",
            {
                **evento,
                "id": str(uuid4()),
                "versao": 2,
                "origem": "executando",
                "destino": "pausada",
            },
        )
    assert a1.estado(db) == antes
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        alterar(conn, "campanha_execucoes", e["id"], 1, estado="executando", atualizado_em=T1)
    assert a1.estado(db) == antes


def test_cas_concorrente_e_diario_unico(execucao):
    (db, _, _, _), e = execucao
    barreira = Barrier(2)

    def disputar():
        barreira.wait(timeout=5)
        with db.engine.begin() as conn:
            return alterar(
                conn, "campanha_execucoes", e["id"], 1, estado="pausada", atualizado_em=T1
            )

    with ThreadPoolExecutor(2) as pool:
        resultados = list(pool.map(lambda _: disputar(), range(2)))
    assert sorted(resultados) == [0, 1]
    with db.engine.begin() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM campanha_execucao_transicoes WHERE entidade='execucao'"
            ).scalar_one()
            == 2
        )


def test_marcador_impede_interrupcao_e_retorno_a_pendente(execucao):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        iniciar(conn, t)
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        alterar(
            conn,
            "tentativas_envio",
            t["id"],
            2,
            estado="interrompida",
            invocacao_em=None,
            finalizada_em=T3,
            atualizado_em=T3,
        )
    assert a1.estado(db) == antes
    with db.engine.begin() as conn:
        alterar(
            conn,
            "campanha_execucoes",
            e["id"],
            1,
            estado="pausando",
            atualizado_em=T3,
            proprietario_id=str(uuid4()),
            geracao=2,
        )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        finalizar(conn, t, "aceita_provedor")
    with db.engine.begin() as conn:
        finalizar(conn, t, "desconhecida")
        alterar(conn, "campanha_execucoes", e["id"], 2, estado="pausada", atualizado_em=T4)


@pytest.mark.parametrize("estado", ["pausada", "concluida", "interrompida"])
def test_execucao_nao_oculta_tentativa_ativa(execucao, estado):
    (db, _, _, _), e = execucao
    with db.engine.begin() as conn:
        reservar(conn, e)
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        alterar(conn, "campanha_execucoes", e["id"], 1, estado=estado, atualizado_em=T3)
    assert a1.estado(db) == antes


@pytest.mark.parametrize("incluir_segunda", [False, True])
def test_ordem_deduplicacao_exclusao_original_e_reserva_global(simulavel, incluir_segunda):
    from app.consentimentos.schemas import IdentidadeEntrada

    sim = a1.anterior.simulacao
    r, campanha, _ = simulavel
    c, app, _, contato, original, _, token, cf = r
    novas = []
    for telefone in ("+442083661177", "+16502530000"):
        phone = c.post(
            "/contatos/" + contato["id"] + "/telefones", json={"telefone": telefone}
        ).json()
        ident = app.state.consentimentos.criar_identidade(
            contato["id"],
            IdentidadeEntrada(
                operacao_id=uuid4(),
                telefone_id=UUID(phone["id"]),
                canal="whatsapp",
                versao=app.state.consentimentos.estado(contato["id"], cf[4]["id"])["versao"],
            ),
            token,
        )
        novas.append(ident)
        assert (
            sim.prep.campanhas.selecionar(
                c, c.get("/campanhas/" + campanha).json(), ident
            ).status_code
            == 200
        )
    if incluir_segunda:
        sim.consent.registrar((*cf[:5], novas[0], token))
    receipt, _ = sim.preparar(simulavel)
    sim.confirmar(simulavel, receipt)
    for ident in novas:
        if ident != novas[0] or not incluir_segunda:
            sim.consent.registrar((*cf[:5], ident, token))
    db = app.state.persistence.database
    with db.engine.begin() as conn:
        a = a1.anterior.dados(conn)
        a1.anterior.inserir(conn, a)
        if incluir_segunda:
            conn.exec_driver_sql(
                "CREATE TRIGGER falha_fila_teste AFTER INSERT ON campanha_execucao_itens "
                "WHEN NEW.ordem=2 BEGIN SELECT RAISE(ABORT,'Falha parcial da fila'); END"
            )
            before = a1.anterior.snapshot(conn)
            with pytest.raises(IntegrityError, match="Falha parcial da fila"):
                criar_execucao(conn, a)
            assert a1.anterior.snapshot(conn) == before
            conn.exec_driver_sql("DROP TRIGGER falha_fila_teste")
        e = criar_execucao(conn, a)
        rows = conn.exec_driver_sql(
            "SELECT id,identidade_contato_id,ordem FROM campanha_execucao_itens ORDER BY ordem"
        ).all()
        esperadas = [original["id"]] + ([novas[0]["id"]] if incluir_segunda else [])
        assert [r[1] for r in rows] == sorted(esperadas)
        assert [r[2] for r in rows] == list(range(1, len(esperadas) + 1))
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM campanha_destinatarios"
        ).scalar_one() == len(esperadas)
        reservar(conn, e)
    if incluir_segunda:
        antes = a1.estado(db)
        with pytest.raises(IntegrityError), db.engine.begin() as conn:
            reservar(conn, e, item_id=rows[1][0])
        assert a1.estado(db) == antes


def test_nova_preparacao_sem_tentativas_reutiliza_so_destinatario_logico(execucao, simulavel):
    (db, a, _, _), e = execucao
    sim = a1.anterior.simulacao
    with db.engine.begin() as conn:
        item = conn.exec_driver_sql("SELECT id FROM campanha_execucao_itens").scalar_one()
        alterar(
            conn,
            "campanha_execucao_itens",
            item,
            1,
            estado="interrompido",
            motivo_codigo="encerramento",
            atualizado_em=T1,
        )
        alterar(conn, "campanha_execucoes", e["id"], 1, estado="interrompida", atualizado_em=T2)
        a1.anterior.revogar(conn, a, data=T2)
        conn.exec_driver_sql(
            "UPDATE campanha_preparacoes SET invalidado_em=? WHERE id=?", (T3, a["preparacao_id"])
        )
        conn.exec_driver_sql(
            "UPDATE campanhas SET status='rascunho',versao=versao+1 WHERE id=?", (a["campanha_id"],)
        )
        anterior = dict(
            conn.exec_driver_sql("SELECT * FROM campanha_execucao_itens").mappings().one()
        )
    receipt, _ = sim.preparar(simulavel)
    sim.confirmar(simulavel, receipt)
    with db.engine.begin() as conn:
        nova = a1.anterior.dados(conn)
        a1.anterior.inserir(conn, nova)
        criar_execucao(conn, nova)
        assert conn.exec_driver_sql("SELECT count(*) FROM campanha_destinatarios").scalar_one() == 1
        assert (
            conn.exec_driver_sql("SELECT count(*) FROM campanha_execucao_itens").scalar_one() == 2
        )
        assert (
            dict(
                conn.exec_driver_sql("SELECT * FROM campanha_execucao_itens WHERE id=?", (item,))
                .mappings()
                .one()
            )
            == anterior
        )


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE dependente(id TEXT REFERENCES campanha_execucao_itens(id))",
        "CREATE VIEW dependente AS SELECT * FROM tentativas_envio",
        "CREATE TRIGGER dependente BEFORE INSERT ON campanha_destinatarios BEGIN SELECT 1; END",
    ],
)
def test_downgrade_recusa_dependencias_operacionais_desconhecidas(vazia, sql):
    db, settings, _ = vazia
    a1.migrar(db, settings)
    with db.engine.begin() as conn:
        conn.exec_driver_sql(sql)
    antes = a1.estado(db)
    with pytest.raises(RuntimeError, match="Esquema 0009 desconhecido"):
        a1.anterior.revision(db, a1.ORIGEM, downgrade=True)
    assert a1.estado(db) == antes


@pytest.mark.parametrize("impedimento", ["revogacao", "conta_inativa"])
def test_impedimento_antes_do_marcador_bloqueia_invocacao(execucao, impedimento):
    (db, a, _, _), e = execucao
    with db.engine.begin() as conn:
        t = reservar(conn, e)
        if impedimento == "revogacao":
            a1.anterior.revogar(conn, a, data=T2)
        else:
            conn.exec_driver_sql(
                "UPDATE contas_envio SET ativo=0,versao=versao+1,atualizado_em=? "
                "WHERE id=(SELECT conta_envio_id FROM campanhas WHERE id=?)",
                (T2, a["campanha_id"]),
            )
    antes = a1.estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        iniciar(conn, t)
    assert a1.estado(db) == antes
    with db.engine.begin() as conn:
        assert (
            conn.exec_driver_sql("SELECT invocacao_em FROM tentativas_envio").scalar_one() is None
        )
        alterar(
            conn,
            "tentativas_envio",
            t["id"],
            1,
            estado="interrompida",
            finalizada_em=T3,
            atualizado_em=T3,
        )
