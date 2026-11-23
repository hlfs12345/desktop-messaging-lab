"""Decisoes de simulacao, sem fila, servico ou alteracao das fotografias."""

from alembic import op

revision = "0009_autorizacoes_simulacao"
down_revision = "0008_contas_mock"
branch_labels = None
depends_on = None


def _data_utc_canonica(coluna):
    # Normalizar so o calendario evita perda/arredondamento dos microssegundos.
    return (
        f"(typeof({coluna})='text' AND length({coluna})=27 AND "
        f"length(CAST({coluna} AS BLOB))=27 AND "
        f"{coluna} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
        "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]."
        "[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND "
        f"substr({coluna},1,4) BETWEEN '0001' AND '9999' AND "
        f"date(substr({coluna},1,10),'+0 days') IS substr({coluna},1,10) AND "
        f"substr({coluna},12,2) BETWEEN '00' AND '23' AND "
        f"substr({coluna},15,2) BETWEEN '00' AND '59' AND "
        f"substr({coluna},18,2) BETWEEN '00' AND '59')"
    )


def upgrade():
    op.execute(f"""CREATE TABLE campanha_autorizacoes_simulacao (
        id TEXT NOT NULL,
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL,
        base_id TEXT NOT NULL,
        impressao TEXT NOT NULL,
        resumo_configuracao TEXT NOT NULL,
        autorizado_por_usuario_id TEXT NOT NULL,
        autorizado_em TEXT NOT NULL,
        estado TEXT NOT NULL,
        versao INTEGER NOT NULL,
        revogado_por_usuario_id TEXT,
        revogado_em TEXT,
        motivo_codigo TEXT,
        CONSTRAINT pk_campanha_autorizacoes_simulacao PRIMARY KEY(id),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_uuid CHECK(length(id)=36),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_resumos CHECK(
            length(impressao)=64 AND impressao NOT GLOB '*[^0-9a-f]*' AND
            length(resumo_configuracao)=64 AND resumo_configuracao NOT GLOB '*[^0-9a-f]*'),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_estado CHECK(
            estado IN ('vigente','revogada')),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_versao CHECK(
            typeof(versao)='integer' AND versao>=1),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_datas CHECK(
            {_data_utc_canonica("autorizado_em")} AND
            (revogado_em IS NULL OR ({_data_utc_canonica("revogado_em")} AND
            revogado_em>=autorizado_em))),
        CONSTRAINT ck_campanha_autorizacoes_simulacao_revogacao CHECK(
            (estado='vigente' AND revogado_por_usuario_id IS NULL AND
            revogado_em IS NULL AND motivo_codigo IS NULL) OR
            (estado='revogada' AND revogado_por_usuario_id IS NOT NULL AND
            revogado_em IS NOT NULL AND motivo_codigo IS NOT NULL AND
            motivo_codigo IN ('revogacao_manual','retorno_rascunho'))),
        CONSTRAINT uq_campanha_autorizacoes_simulacao_preparacao_id UNIQUE(preparacao_id),
        CONSTRAINT fk_campanha_autorizacoes_simulacao_campanha_id_campanhas
            FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_autorizacoes_simulacao_preparacao_id_campanha_preparacoes
            FOREIGN KEY(preparacao_id) REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_autorizacoes_simulacao_base_id_campanha_contexto_criptografico
            FOREIGN KEY(base_id) REFERENCES campanha_contexto_criptografico(base_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_autorizacoes_simulacao_autorizado_por_usuario_id_usuarios
            FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_autorizacoes_simulacao_revogado_por_usuario_id_usuarios
            FOREIGN KEY(revogado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE UNIQUE INDEX ix_autorizacoes_simulacao_vigente ON "
        "campanha_autorizacoes_simulacao(campanha_id) WHERE estado='vigente'"
    )
    op.execute(
        "CREATE INDEX ix_autorizacoes_simulacao_historico ON "
        "campanha_autorizacoes_simulacao(campanha_id,autorizado_em,id)"
    )
    # SQL confere referencias e ciclo de vida; AEAD/configuracao/sessao cabem ao Marco B.
    op.execute("""CREATE TRIGGER autorizacoes_simulacao_origem BEFORE INSERT ON
        campanha_autorizacoes_simulacao WHEN NEW.estado!='vigente' OR NEW.versao!=1
        OR NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c
        ON c.id=p.campanha_id WHERE p.id=NEW.preparacao_id AND
        p.campanha_id=NEW.campanha_id AND p.base_id=NEW.base_id AND
        p.impressao=NEW.impressao AND p.confirmado_em IS NOT NULL AND
        p.confirmado_em<=NEW.autorizado_em AND p.invalidado_em IS NULL AND
        c.status='preparada')
        BEGIN SELECT RAISE(ABORT,'Origem da autorizacao incompativel'); END""")
    # BEFORE INSERT protege conflitos em TODAS as chaves, mesmo sem recursive_triggers.
    op.execute("""CREATE TRIGGER autorizacoes_simulacao_sem_replace BEFORE INSERT ON
        campanha_autorizacoes_simulacao WHEN EXISTS(SELECT 1 FROM
        campanha_autorizacoes_simulacao WHERE id=NEW.id OR preparacao_id=NEW.preparacao_id
        OR (campanha_id=NEW.campanha_id AND estado='vigente' AND NEW.estado='vigente'))
        BEGIN SELECT RAISE(ABORT,'Autorizacao existente'); END""")
    fixed = (
        "id",
        "campanha_id",
        "preparacao_id",
        "base_id",
        "impressao",
        "resumo_configuracao",
        "autorizado_por_usuario_id",
        "autorizado_em",
    )
    immutable = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in fixed)
    op.execute(f"""CREATE TRIGGER autorizacoes_simulacao_transicao BEFORE UPDATE ON
        campanha_autorizacoes_simulacao WHEN {immutable}
        OR OLD.estado!='vigente' OR NEW.estado!='revogada' OR NEW.versao!=OLD.versao+1
        BEGIN SELECT RAISE(ABORT,'Decisao imutavel ou transicao invalida'); END""")
    op.execute("""CREATE TRIGGER autorizacoes_simulacao_sem_delete BEFORE DELETE ON
        campanha_autorizacoes_simulacao
        BEGIN SELECT RAISE(ABORT,'Preserve o historico de autorizacoes'); END""")
    op.execute("""CREATE TRIGGER preparacao_autorizada_sem_invalidacao BEFORE UPDATE OF
        invalidado_em ON campanha_preparacoes WHEN NEW.invalidado_em IS NOT NULL AND
        EXISTS(SELECT 1 FROM campanha_autorizacoes_simulacao
        WHERE preparacao_id=OLD.id AND estado='vigente')
        BEGIN SELECT RAISE(ABORT,'Revogue a autorizacao antes de invalidar'); END""")


def downgrade():
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM campanha_autorizacoes_simulacao LIMIT 1")
        .first()
        is not None
    ):
        raise RuntimeError("Downgrade recusado: preserve autorizacoes e decisoes historicas.")
    op.execute("DROP TRIGGER preparacao_autorizada_sem_invalidacao")
    op.drop_table("campanha_autorizacoes_simulacao")
