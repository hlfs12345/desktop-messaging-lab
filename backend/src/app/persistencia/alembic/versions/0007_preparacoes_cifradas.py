"""Fotografias cifradas, sem fila ou envio. Preserva 0006 integralmente."""

import re

from alembic import op

revision = "0007_preparacoes_cifradas"
down_revision = "0006_campanhas_rascunho"
branch_labels = None
depends_on = None


def reconstruir(preparada):
    conn = op.get_bind()
    original = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='campanhas'"
    ).scalar_one()
    extras = (
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name='campanhas' AND type"
            " IN ('index','trigger') AND sql IS NOT NULL"
        )
        .scalars()
        .all()
    )
    children = {}
    for name in ("campanha_listas", "campanha_selecoes"):
        children[name] = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name=?", (name,)
        ).scalar_one()
        conn.exec_driver_sql(f"CREATE TEMP TABLE copia_{name} AS SELECT * FROM {name}")
        conn.exec_driver_sql(f"DROP TABLE {name}")
    sql = re.sub(r'CREATE TABLE "?campanhas"?', "CREATE TABLE campanhas_0007", original, count=1)
    sql = (
        sql.replace("status='rascunho'", "status IN ('rascunho','preparada')")
        if preparada
        else sql.replace("status IN ('rascunho','preparada')", "status='rascunho'")
    )
    conn.exec_driver_sql(sql)
    conn.exec_driver_sql("INSERT INTO campanhas_0007 SELECT * FROM campanhas")
    conn.exec_driver_sql("DROP TABLE campanhas")
    conn.exec_driver_sql("ALTER TABLE campanhas_0007 RENAME TO campanhas")
    for sql in extras:
        conn.exec_driver_sql(sql)
    for name, sql in children.items():
        conn.exec_driver_sql(sql)
        conn.exec_driver_sql(f"INSERT INTO {name} SELECT * FROM copia_{name}")
        conn.exec_driver_sql(f"DROP TABLE copia_{name}")


def upgrade():
    reconstruir(True)
    # DDL explicito e independente da metadata mutavel da aplicacao.
    op.execute("""CREATE TABLE campanha_contexto_criptografico (
        id TEXT NOT NULL PRIMARY KEY CHECK(id='campanhas-v1'),
        base_id TEXT NOT NULL CHECK(length(base_id)=36),
        chave_dpapi BLOB NOT NULL CHECK(length(chave_dpapi) BETWEEN 1 AND 65536),
        nonce BLOB NOT NULL CHECK(length(nonce)=12),
        verificador BLOB NOT NULL CHECK(length(verificador)=48),
        conferido INTEGER NOT NULL CHECK(conferido IN (0,1)), criado_em TEXT NOT NULL,
        CONSTRAINT uq_campanha_contexto_criptografico_base_id UNIQUE(base_id))""")
    op.execute("""CREATE TABLE campanha_preparacoes (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        campanha_id TEXT NOT NULL,
        base_id TEXT NOT NULL,
        versao_origem INTEGER NOT NULL CHECK(versao_origem>=1),
        revisao_pos INTEGER NOT NULL CHECK(revisao_pos>=1), fotografia_json TEXT NOT NULL,
        chave_nonce BLOB NOT NULL CHECK(length(chave_nonce)=12),
        chave_cifrada BLOB NOT NULL CHECK(length(chave_cifrada)=48),
        manifesto_nonce BLOB NOT NULL CHECK(length(manifesto_nonce)=12),
        manifesto BLOB NOT NULL CHECK(length(manifesto)=48),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        incluidos INTEGER NOT NULL CHECK(incluidos>=1), excluidos INTEGER NOT NULL
CHECK(excluidos>=0 AND incluidos+excluidos<=10000),
        bytes_texto INTEGER NOT NULL CHECK(bytes_texto BETWEEN 1 AND 67108864),
        criado_por_usuario_id TEXT NOT NULL,
        criado_em TEXT NOT NULL, confirmado_por_usuario_id TEXT,
        confirmado_em TEXT, invalidado_em TEXT,
        CHECK((confirmado_em IS NULL AND confirmado_por_usuario_id IS NULL) OR
(confirmado_em>=criado_em AND confirmado_por_usuario_id IS NOT NULL)),
        CHECK(invalidado_em IS NULL OR invalidado_em>=criado_em),
        CONSTRAINT fk_campanha_preparacoes_base_id_campanha_contexto_criptografico
        FOREIGN KEY(base_id) REFERENCES campanha_contexto_criptografico(base_id) ON DELETE
        RESTRICT,
        CONSTRAINT fk_campanha_preparacoes_campanha_id_campanhas FOREIGN KEY(campanha_id)
REFERENCES campanhas(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_preparacoes_criado_por_usuario_id_usuarios FOREIGN
KEY(criado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_preparacoes_confirmado_por_usuario_id_usuarios FOREIGN
KEY(confirmado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE UNIQUE INDEX ix_preparacoes_vigente ON "
        "campanha_preparacoes(campanha_id) WHERE invalidado_em IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_preparacoes_historico ON campanha_preparacoes(campanha_id,criado_em)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_preparacoes_nonce ON campanha_preparacoes(base_id,chave_nonce)"
    )
    op.execute("""CREATE TABLE campanha_preparacao_itens (
        preparacao_id TEXT NOT NULL,
        identidade_contato_id TEXT NOT NULL,
        incluido INTEGER NOT NULL, motivos_json TEXT NOT NULL,
        evento_consentimento_id TEXT,
        nonce BLOB, conteudo_cifrado BLOB, bytes_texto INTEGER NOT NULL,
        PRIMARY KEY(preparacao_id,identidade_contato_id),
        CHECK((incluido=1 AND nonce IS NOT NULL AND length(nonce)=12 AND conteudo_cifrado IS
NOT NULL AND length(conteudo_cifrado)=bytes_texto+16 AND bytes_texto BETWEEN 1 AND 65536 AND
motivos_json='[]') OR (incluido=0 AND nonce IS NULL AND conteudo_cifrado IS NULL AND
bytes_texto=0 AND motivos_json!='[]')),
        CONSTRAINT fk_campanha_preparacao_itens_preparacao_id_campanha_preparacoes FOREIGN
KEY(preparacao_id) REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_preparacao_itens_identidade_contato_id_identidades_contato
FOREIGN KEY(identidade_contato_id) REFERENCES identidades_contato(id) ON DELETE RESTRICT,
        CONSTRAINT
fk_campanha_preparacao_itens_evento_consentimento_id_eventos_consentimento FOREIGN
KEY(evento_consentimento_id) REFERENCES eventos_consentimento(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE INDEX ix_preparacao_itens_pagina ON "
        "campanha_preparacao_itens(preparacao_id,incluido,identidade_contato_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_preparacao_itens_nonce ON "
        "campanha_preparacao_itens(preparacao_id,nonce)"
    )
    for table in ("auditoria", "campanha_preparacao_itens"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_sem_{action.lower()} BEFORE {action} ON "
                f"{table} BEGIN SELECT RAISE(ABORT,'Registro imutavel'); END"
            )
        condition = (
            "id=NEW.id"
            if table == "auditoria"
            else (
                "preparacao_id=NEW.preparacao_id AND "
                "identidade_contato_id=NEW.identidade_contato_id"
            )
        )
        op.execute(
            f"CREATE TRIGGER {table}_sem_replace BEFORE INSERT ON {table} WHEN "
            f"EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT "
            f"RAISE(ABORT,'Registro existente'); END"
        )
    for table in ("campanha_preparacoes", "campanha_contexto_criptografico"):
        op.execute(
            f"CREATE TRIGGER {table}_sem_delete BEFORE DELETE ON {table} BEGIN "
            f"SELECT RAISE(ABORT,'Preserve o historico'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_sem_replace BEFORE INSERT ON {table} WHEN "
            f"EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id) BEGIN SELECT "
            f"RAISE(ABORT,'Registro existente'); END"
        )
    fixed = (
        "id",
        "campanha_id",
        "base_id",
        "versao_origem",
        "revisao_pos",
        "fotografia_json",
        "chave_nonce",
        "chave_cifrada",
        "manifesto_nonce",
        "manifesto",
        "impressao",
        "incluidos",
        "excluidos",
        "bytes_texto",
        "criado_por_usuario_id",
        "criado_em",
    )
    condition = " OR ".join(f"NEW.{x} IS NOT OLD.{x}" for x in fixed)
    op.execute(f"""CREATE TRIGGER preparacoes_fixas BEFORE UPDATE ON campanha_preparacoes
        WHEN {condition}
        OR (OLD.invalidado_em IS NOT NULL AND (NEW.invalidado_em IS NOT OLD.invalidado_em OR
NEW.confirmado_em IS NOT OLD.confirmado_em OR NEW.confirmado_por_usuario_id IS NOT
OLD.confirmado_por_usuario_id))
        OR (OLD.confirmado_em IS NOT NULL AND (NEW.confirmado_em IS NOT OLD.confirmado_em OR
NEW.confirmado_por_usuario_id IS NOT OLD.confirmado_por_usuario_id))
        BEGIN SELECT RAISE(ABORT,'Fotografia imutavel'); END""")
    op.execute(
        "CREATE TRIGGER contexto_fixo BEFORE UPDATE ON "
        "campanha_contexto_criptografico WHEN NEW.id!=OLD.id OR "
        "NEW.base_id!=OLD.base_id OR NEW.nonce!=OLD.nonce OR "
        "NEW.verificador!=OLD.verificador OR NEW.criado_em!=OLD.criado_em "
        "BEGIN SELECT RAISE(ABORT,'Contexto imutavel'); END"
    )
    op.execute(
        "CREATE TRIGGER campanha_congelada BEFORE UPDATE ON campanhas WHEN"
        " OLD.status='preparada' AND (NEW.nome IS NOT OLD.nome OR "
        "NEW.nome_busca IS NOT OLD.nome_busca OR NEW.conta_envio_id IS NOT"
        " OLD.conta_envio_id OR NEW.escopo_id IS NOT OLD.escopo_id OR "
        "NEW.template_versao_id IS NOT OLD.template_versao_id OR "
        "NEW.categoria IS NOT OLD.categoria) BEGIN SELECT "
        "RAISE(ABORT,'Campanha congelada'); END"
    )
    for action in ("INSERT", "UPDATE"):
        op.execute(
            f"CREATE TRIGGER campanha_estado_{action.lower()} BEFORE {action} ON campanhas "
            "WHEN (NEW.status='preparada' AND NOT EXISTS(SELECT 1 FROM "
            "campanha_preparacoes WHERE campanha_id=NEW.id AND invalidado_em IS NULL)) "
            "OR (NEW.status='rascunho' AND EXISTS(SELECT 1 FROM campanha_preparacoes "
            "WHERE campanha_id=NEW.id AND invalidado_em IS NULL)) "
            "BEGIN SELECT RAISE(ABORT,'Preparacao vigente incompativel'); END"
        )
    for table in ("campanha_listas", "campanha_selecoes"):
        for action in ("INSERT", "UPDATE", "DELETE"):
            row = "OLD" if action == "DELETE" else "NEW"
            parent = f"id={row}.campanha_id"
            if action == "UPDATE":
                parent = "(id=OLD.campanha_id OR id=NEW.campanha_id)"
            op.execute(
                f"CREATE TRIGGER {table}_congelada_{action.lower()} BEFORE {action}"
                f" ON {table} WHEN EXISTS(SELECT 1 FROM campanhas WHERE "
                f"{parent} AND status='preparada') BEGIN SELECT "
                f"RAISE(ABORT,'Selecao congelada'); END"
            )


def downgrade():
    conn = op.get_bind()
    if (
        conn.exec_driver_sql("SELECT count(*) FROM campanha_contexto_criptografico").scalar()
        or conn.exec_driver_sql("SELECT count(*) FROM campanha_preparacoes").scalar()
    ):
        raise RuntimeError("Downgrade recusado: preserve chaves e fotografias.")
    # Conservador: nenhuma campanha pode existir durante esta reconstrucao reversa.
    if conn.exec_driver_sql("SELECT count(*) FROM campanhas").scalar():
        raise RuntimeError("Downgrade recusado: preserve campanhas.")
    names = (
        conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'").scalars().all()
    )
    for name in names:
        if name.startswith(
            (
                "auditoria_sem_",
                "campanha_preparacao_itens_",
                "campanha_preparacoes_",
                "campanha_contexto_",
                "preparacoes_fixas",
                "contexto_fixo",
                "campanha_congelada",
                "campanha_estado_",
                "campanha_listas_congelada",
                "campanha_selecoes_congelada",
            )
        ):
            op.execute('DROP TRIGGER "' + name + '"')
    op.drop_table("campanha_preparacao_itens")
    op.drop_table("campanha_preparacoes")
    op.drop_table("campanha_contexto_criptografico")
    reconstruir(False)
