"""Entrega 18 A1: variantes historicas da 0009 e endurecimento aditivo real."""

from contextlib import contextmanager

import pytest
import test_autorizacoes_simulacao_migration as anterior
from alembic.script import ScriptDirectory
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.campanhas.models import AutorizacaoSimulacao
from app.persistencia.database import Database
from app.persistencia.errors import MigrationError, SnapshotError
from app.persistencia.migrations import bundled_migrations, migrate, migration_config, read_revision
from app.persistencia.models import utc_text

cadastro = anterior.cadastro
consentimento = anterior.consentimento
rascunho = anterior.rascunho
pronta = anterior.pronta
simulavel = anterior.simulavel
populada_0008 = anterior.populada_0008
populada_0007 = anterior.historico.populada_0007

ORIGEM = "0009_autorizacoes_simulacao"
HEAD = "0010_autorizacoes_utc"
TABLE = anterior.TABLE
# Expressao da 0009 anterior a P3, recuperada do registro da Entrega 17.
# Fixture independente da implementacao da 0010; demais objetos sao os da 0009 congelada.
CHECK_ORIGINAL = """CONSTRAINT ck_campanha_autorizacoes_simulacao_datas CHECK(
    length(autorizado_em)=27 AND autorizado_em GLOB '????-??-??T??:??:??.??????Z' AND
    julianday(autorizado_em) IS NOT NULL AND
    (revogado_em IS NULL OR (length(revogado_em)=27 AND
    revogado_em GLOB '????-??-??T??:??:??.??????Z' AND
    julianday(revogado_em) IS NOT NULL AND revogado_em>=autorizado_em))),
    """


def modulo():
    with bundled_migrations() as location:
        return ScriptDirectory.from_config(migration_config(location)).get_revision(HEAD).module


@contextmanager
def variante_original(db, variante):
    def ddl(_conn, _cursor, sql, parameters, _context, _many):
        if variante == "original" and sql.startswith("CREATE TABLE " + TABLE):
            start = sql.index("CONSTRAINT ck_campanha_autorizacoes_simulacao_datas")
            end = sql.index("CONSTRAINT ck_campanha_autorizacoes_simulacao_revogacao")
            sql = sql[:start] + CHECK_ORIGINAL + sql[end:]
        return sql, parameters

    event.listen(db.engine, "before_cursor_execute", ddl, retval=True)
    try:
        yield
    finally:
        event.remove(db.engine, "before_cursor_execute", ddl)


@pytest.fixture(params=["original", "corrigida"])
def vazia(settings, request):
    settings.data_dir.mkdir(parents=True)
    settings.database_path.touch()
    db = Database(settings.database_path)
    try:
        with variante_original(db, request.param):
            anterior.revision(db, ORIGEM)
        yield db, settings, request.param
    finally:
        db.close()


@pytest.fixture(params=["original", "corrigida"])
def povoada(populada_0008, request):
    _, app, *_ = populada_0008
    db = app.state.persistence.database
    with variante_original(db, request.param):
        anterior.revision(db, ORIGEM)
    return db, app.state.persistence.settings, request.param, populada_0008


def estado(db):
    with db.engine.begin() as conn:
        return (
            anterior.snapshot(conn),
            anterior.objects(conn),
            conn.exec_driver_sql("SELECT * FROM alembic_version").all(),
        )


def anteriores(db):
    dados, objetos, _ = estado(db)
    dados, objetos = anterior.historico.sem_telegram(dados, objetos)
    novos = modulo()._objetos_operacionais()
    for tabela in modulo().TABELAS_OPERACIONAIS:
        assert dados.pop(tabela) == []
    objetos = [r for r in objetos if tuple(r[:3]) not in novos and r[1] not in modulo().PROTECOES]
    return dados, objetos


def migrar(db, settings):
    with bundled_migrations() as location:
        return migrate(
            db.engine,
            settings.database_path,
            settings.snapshots_dir,
            ORIGEM,
            migration_config(location),
            existing=True,
        )


def auditar(conn, value):
    identifier = str(anterior.uuid4())
    conn.exec_driver_sql(
        "INSERT INTO auditoria VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            value["autorizado_por_usuario_id"],
            "teste_utc",
            "campanha",
            value["campanha_id"],
            "{}",
            utc_text(anterior.agora()),
        ),
    )
    assert (
        conn.exec_driver_sql("SELECT id FROM auditoria WHERE id=?", (identifier,)).scalar_one()
        == identifier
    )


def test_reconhecimento_repetivel_cadeia_e_tabelas_operacionais_vazias(vazia):
    db, settings, variante = vazia
    before = estado(db)
    with db.engine.begin() as conn:
        assert modulo()._validar(conn) == variante
        assert modulo()._validar(conn) == variante
    assert estado(db) == before
    migrar(db, settings)
    after = estado(db)
    assert anteriores(db) == before[:2]
    assert read_revision(settings.database_path) == "0012_telegram_recepcao"
    with db.engine.begin() as conn:
        assert modulo()._validar(conn, protegida=True) == variante
        assert modulo()._validar(conn, protegida=True) == variante
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    anterior.revision(db, HEAD)  # Reabrir o alvo ja aplicado nao recria triggers.
    assert estado(db) == after


@pytest.mark.parametrize("revogada", [False, True])
def test_preserva_autorizacoes_fotografias_blobs_e_semantica(povoada, revogada):
    db, settings, variante, contexto = povoada
    c, _, root, identidade, fotografias = contexto
    conteudos = {
        pid: c.get(root + "/preparacoes/" + pid + "/itens/" + identidade["id"] + "/conteudo").json()
        for pid in fotografias
    }
    with db.engine.begin() as conn:
        value = anterior.dados(conn, autorizado_em="2030-01-01T23:59:59.123456Z")
        anterior.inserir(conn, value)
        if revogada:
            anterior.revogar(conn, value, data="2030-01-02T23:59:59.999999Z")
    before = estado(db)
    saved = migrar(db, settings)
    assert read_revision(saved) == ORIGEM
    assert anteriores(db) == before[:2]  # Inclui autorizacoes, auditoria e todos os BLOBs.
    with db.engine.begin() as conn:
        assert modulo()._validar(conn, protegida=True) == variante
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    for pid in fotografias:
        assert c.get(root + "/preparacoes/" + pid).json()["integridade"] == "verificada"
        path = root + "/preparacoes/" + pid + "/itens/" + identidade["id"] + "/conteudo"
        assert c.get(path).json() == conteudos[pid]
    if not revogada:
        with db.engine.begin() as conn:
            anterior.revogar(conn, value, data="2030-01-02T23:59:59.999999Z")
        with db.transaction() as session:
            item = session.scalar(select(AutorizacaoSimulacao))
            assert utc_text(item.autorizado_em) == value["autorizado_em"]
            assert utc_text(item.revogado_em) == "2030-01-02T23:59:59.999999Z"
        with pytest.raises(IntegrityError), db.engine.begin() as conn:
            anterior.revogar(conn, value, versao=3)


@pytest.mark.parametrize("variante", ["original", "corrigida"])
def test_preserva_fotografias_reais_antigas(populada_0007, variante):
    c, app, root, identidade, fotografias = populada_0007
    db = app.state.persistence.database
    with variante_original(db, variante):
        anterior.revision(db, ORIGEM)
    before = estado(db)
    migrar(db, app.state.persistence.settings)
    assert anteriores(db) == before[:2]
    for pid in fotografias:
        detalhe = c.get(root + "/preparacoes/" + pid)
        assert detalhe.status_code == 200 and detalhe.json()["integridade"] == "verificada"
        assert "configuracao_simulacao" not in detalhe.json()
        path = root + "/preparacoes/" + pid + "/itens/" + identidade["id"] + "/conteudo"
        assert c.get(path).status_code == 200


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
@pytest.mark.parametrize("valor", anterior.DATAS_INVALIDAS)
def test_insert_e_revogacao_invalidos_revertem_escrita_anterior(povoada, campo, valor):
    db, settings, _, _ = povoada
    migrar(db, settings)
    with db.engine.begin() as conn:
        # Isolar calendario da cronologia da preparacao, exclusivamente nesta fixture.
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_origem")
        value = anterior.dados(conn, autorizado_em="0001-01-01T00:00:00.000001Z")
        if campo == "revogado_em":
            anterior.inserir(conn, value)
    before = estado(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        auditar(conn, value)
        if campo == "autorizado_em":
            anterior.inserir(conn, {**value, campo: valor})
        else:
            anterior.revogar(conn, value, data=valor)
    assert estado(db) == before


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
@pytest.mark.parametrize(
    "valor", ["2030-11-31T12:00:00.123456Z", "2030-01-01T12:00:00.123456Z\x00"]
)
def test_update_direto_protegido_sem_depender_da_imutabilidade(povoada, campo, valor):
    db, settings, _, _ = povoada
    migrar(db, settings)
    with db.engine.begin() as conn:
        value = anterior.dados(conn)
        anterior.inserir(conn, value)
        # Somente na fixture descartavel: provar o trigger UTC sem o bloqueio preexistente.
        conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_transicao")
    before = estado(db)
    with pytest.raises(IntegrityError, match="Datas UTC invalidas"), db.engine.begin() as conn:
        auditar(conn, value)
        conn.exec_driver_sql(f"UPDATE {TABLE} SET {campo}=? WHERE id=?", (valor, value["id"]))
    assert estado(db) == before


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
@pytest.mark.parametrize(
    "valor", ["2030-11-31T12:00:00.123456Z", "2030-11-30T12:00:00.123456Z\x00extra"]
)
def test_historico_invalido_original_recusado_sem_normalizacao(povoada, campo, valor):
    db, settings, variante, _ = povoada
    if variante != "original":
        # Corrigida tambem deve detectar corrupcao historica: bypass so na fixture.
        bypass = True
    else:
        bypass = False
    with db.engine.begin() as conn:
        if bypass:
            conn.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        value = anterior.dados(conn, autorizado_em="2030-11-01T00:00:00.000001Z")
        if campo == "autorizado_em":
            value[campo] = valor
        anterior.inserir(conn, value)
        if campo == "revogado_em":
            anterior.revogar(conn, value, data=valor)
    before = estado(db)
    with pytest.raises(RuntimeError, match="Datas historicas invalidas"):
        anterior.revision(db, HEAD)
    assert estado(db) == before
    # Na corrigida, quick_check do snapshot ja detecta a CHECK violada pela corrupcao.
    with pytest.raises(MigrationError if variante == "original" else SnapshotError):
        migrar(db, settings)
    assert estado(db) == before


@pytest.mark.parametrize(
    "alteracao",
    [
        "indice_ausente",
        "indice_diferente",
        "trigger_ausente",
        "trigger_diferente",
        "trigger_extra",
        "view_dependente",
        "fk_dependente",
        "coluna_extra",
        "tipo",
        "tipo_separado",
        "fk",
        "nulabilidade",
        "constraint",
        "default",
        "literal",
        "protecao_preexistente",
    ],
)
def test_esquemas_desconhecidos_recusados(vazia, alteracao):
    db, settings, _ = vazia
    with db.engine.begin() as conn:
        if alteracao in {"indice_ausente", "indice_diferente"}:
            conn.exec_driver_sql("DROP INDEX ix_autorizacoes_simulacao_vigente")
            if alteracao == "indice_diferente":
                conn.exec_driver_sql(
                    f"CREATE INDEX ix_autorizacoes_simulacao_vigente ON {TABLE}(campanha_id)"
                )
        elif alteracao in {"trigger_ausente", "trigger_diferente"}:
            conn.exec_driver_sql("DROP TRIGGER autorizacoes_simulacao_sem_delete")
            if alteracao == "trigger_diferente":
                conn.exec_driver_sql(
                    "CREATE TRIGGER autorizacoes_simulacao_sem_delete BEFORE DELETE ON "
                    f"{TABLE} BEGIN SELECT 1; END"
                )
        elif alteracao in {"trigger_extra", "protecao_preexistente"}:
            nome = "extra" if alteracao == "trigger_extra" else next(iter(modulo().PROTECOES))
            conn.exec_driver_sql(
                f"CREATE TRIGGER {nome} AFTER INSERT ON {TABLE} BEGIN SELECT 1; END"
            )
        elif alteracao == "view_dependente":
            conn.exec_driver_sql(f"CREATE VIEW extra AS SELECT * FROM {TABLE}")
        elif alteracao == "fk_dependente":
            conn.exec_driver_sql(f"CREATE TABLE extra(id TEXT REFERENCES {TABLE}(id))")
        elif alteracao == "coluna_extra":
            conn.exec_driver_sql(f"ALTER TABLE {TABLE} ADD COLUMN extra TEXT")
        else:
            ddl = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE name=?", (TABLE,)
            ).scalar_one()
            mudancas = {
                "tipo": ("versao INTEGER NOT NULL", "versao TEXT NOT NULL"),
                "tipo_separado": ("versao INTEGER NOT NULL", "versao INTE GER NOT NULL"),
                "fk": ("ON DELETE RESTRICT", "ON DELETE CASCADE"),
                "nulabilidade": ("estado TEXT NOT NULL", "estado TEXT"),
                "constraint": ("length(id)=36", "length(id)>=1"),
                "default": ("estado TEXT NOT NULL", "estado TEXT NOT NULL DEFAULT 'vigente'"),
                "literal": ("'vigente','revogada'", "'vigente','revogada '"),
            }
            # Recriar apenas a tabela vazia da fixture com DDL divergente, sem writable_schema.
            objetos = (
                conn.exec_driver_sql(
                    "SELECT sql FROM sqlite_schema WHERE tbl_name=? AND type!='table' "
                    "AND sql IS NOT NULL",
                    (TABLE,),
                )
                .scalars()
                .all()
            )
            conn.exec_driver_sql("DROP TABLE " + TABLE)
            conn.exec_driver_sql(ddl.replace(*mudancas[alteracao]))
            for sql in objetos:
                conn.exec_driver_sql(sql)
    before = estado(db)
    with pytest.raises(MigrationError):
        migrar(db, settings)
    assert estado(db) == before


@pytest.mark.parametrize("etapa", ["insert", "update", "versao", "commit"])
def test_rollback_integral_apos_ddl_e_escrita(vazia, etapa):
    db, settings, _ = vazia
    before = estado(db)
    vistos = []

    def falhar(_conn, _cursor, sql, *_):
        alvo = (
            f"CREATE TRIGGER autorizacoes_simulacao_utc_{etapa}_0010"
            if etapa in {"insert", "update"}
            else "UPDATE alembic_version"
        )
        if sql.startswith(alvo):
            vistos.append(sql)
            raise RuntimeError("Falha A1 apos escrita")

    def falhar_commit(_conn):
        vistos.append("commit")
        raise RuntimeError("Falha A1 no commit")

    evento, callback = (
        ("commit", falhar_commit) if etapa == "commit" else ("after_cursor_execute", falhar)
    )
    event.listen(db.engine, evento, callback)
    try:
        with pytest.raises(MigrationError):
            migrar(db, settings)
    finally:
        event.remove(db.engine, evento, callback)
    assert vistos and estado(db) == before
    migrar(db, settings)
    assert read_revision(settings.database_path) == "0012_telegram_recepcao"


def test_downgrade_vazio_seguro_ou_recusado_sem_reexpor_original(vazia):
    db, settings, variante = vazia
    before = estado(db)
    migrar(db, settings)
    after = estado(db)
    if variante == "original":
        with pytest.raises(RuntimeError, match="Downgrade recusado"):
            anterior.revision(db, ORIGEM, downgrade=True)
        assert estado(db) == after
    else:
        anterior.revision(db, ORIGEM, downgrade=True)
        assert estado(db) == before
        anterior.revision(db, HEAD)


@pytest.mark.parametrize("revogada", [False, True])
def test_downgrade_com_historico_recusado(povoada, revogada):
    db, settings, _, _ = povoada
    with db.engine.begin() as conn:
        value = anterior.dados(conn)
        anterior.inserir(conn, value)
        if revogada:
            anterior.revogar(conn, value)
    migrar(db, settings)
    before = estado(db)
    with pytest.raises(RuntimeError, match="Downgrade recusado"):
        anterior.revision(db, ORIGEM, downgrade=True)
    assert estado(db) == before


def test_downgrade_dependencia_desconhecida_recusado(vazia):
    db, settings, _ = vazia
    migrar(db, settings)
    with db.engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE dependencia(id TEXT REFERENCES {TABLE}(id))")
    before = estado(db)
    with pytest.raises(RuntimeError, match="Esquema 0009 desconhecido"):
        anterior.revision(db, ORIGEM, downgrade=True)
    assert estado(db) == before


@pytest.mark.parametrize("campo", ["autorizado_em", "revogado_em"])
def test_datas_validas_e_microssegundos_apos_upgrade(povoada, campo):
    db, settings, _, _ = povoada
    migrar(db, settings)
    with db.engine.begin() as conn:
        for valor in (
            "2032-02-29T12:00:00.000001Z",
            "2032-02-29T23:59:59.999999Z",
            "2030-11-30T23:59:59.123456Z",
        ):
            # Cada savepoint desfaz a propria massa; a mesma preparacao nao e duplicada.
            with conn.begin_nested() as savepoint:
                value = anterior.dados(
                    conn,
                    autorizado_em=valor
                    if campo == "autorizado_em"
                    else "2030-01-01T00:00:00.000001Z",
                )
                anterior.inserir(conn, value)
                if campo == "revogado_em":
                    anterior.revogar(conn, value, data=valor)
                assert (
                    conn.exec_driver_sql(
                        f"SELECT {campo} FROM {TABLE} WHERE id=?", (value["id"],)
                    ).scalar_one()
                    == valor
                )
                savepoint.rollback()
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_historico_com_fk_invalida_recusado(povoada):
    db, settings, _, _ = povoada
    # Corrupcao deliberada apenas na base descartavel, preservando o DDL conhecido.
    with db.engine.begin() as conn:
        driver = conn.connection.driver_connection
        driver.rollback()
        driver.autocommit = True
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        driver.autocommit = False
        value = anterior.dados(conn, autorizado_por_usuario_id=str(anterior.uuid4()))
        anterior.inserir(conn, value)
    before = estado(db)
    with db.engine.begin() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        with pytest.raises(RuntimeError, match="Integridade referencial invalida"):
            modulo()._validar(conn)
    with pytest.raises(MigrationError):
        migrar(db, settings)
    assert estado(db) == before


def test_validacao_exige_fk_e_rollback_downgrade_apos_drop(vazia):
    db, settings, variante = vazia
    with db.engine.begin() as conn:
        driver = conn.connection.driver_connection
        driver.rollback()
        driver.autocommit = True
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        driver.autocommit = False
        with pytest.raises(RuntimeError, match="integridade referencial"):
            modulo()._validar(conn)
    migrar(db, settings)
    if variante != "corrigida":
        return
    before = estado(db)

    def falhar(_conn, _cursor, sql, *_):
        if sql.startswith("DROP TRIGGER autorizacoes_simulacao_utc_update_0010"):
            raise RuntimeError("Falha apos DROP")

    event.listen(db.engine, "after_cursor_execute", falhar)
    try:
        with pytest.raises(RuntimeError, match="Falha apos DROP"):
            anterior.revision(db, ORIGEM, downgrade=True)
    finally:
        event.remove(db.engine, "after_cursor_execute", falhar)
    assert estado(db) == before
