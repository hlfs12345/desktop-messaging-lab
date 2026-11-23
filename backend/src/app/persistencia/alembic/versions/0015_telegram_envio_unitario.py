"""Autorizacao e tentativa imutaveis para um envio Telegram."""

from alembic import op

revision = "0015_telegram_envio_unitario"
down_revision = "0014_telegram_campanhas"
branch_labels = None
depends_on = None


def _recusar(nome, tabela, momento, condicao=None, mensagem="Registro imutavel"):
    quando = "" if condicao is None else f" WHEN {condicao}"
    op.execute(
        f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} "
        f"BEGIN SELECT RAISE(ABORT,'{mensagem}'); END"
    )


def upgrade():
    op.execute(
        "CREATE UNIQUE INDEX uq_preparacao_telegram_vinculo ON "
        "campanha_preparacao_itens_telegram"
        "(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)"
    )
    op.execute("""CREATE TABLE campanha_autorizacoes_envio_telegram (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        operacao_id TEXT NOT NULL UNIQUE CHECK(length(operacao_id)=36),
        requisicao_hash TEXT NOT NULL CHECK(length(requisicao_hash)=64),
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        evento_telegram_id TEXT NOT NULL,
        bot_externo_id TEXT NOT NULL,
        chat_externo_id TEXT NOT NULL,
        conta_versao INTEGER NOT NULL CHECK(conta_versao>=1),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        conteudo_hash TEXT NOT NULL CHECK(length(conteudo_hash)=64),
        autorizado_por_usuario_id TEXT NOT NULL,
        autorizado_em TEXT NOT NULL,
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          REFERENCES campanha_preparacao_itens_telegram
          (preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(evento_telegram_id) REFERENCES eventos_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE INDEX ix_autorizacoes_envio_telegram_campanha ON "
        "campanha_autorizacoes_envio_telegram(campanha_id,autorizado_em)"
    )
    op.execute("""CREATE TABLE campanha_tentativas_envio_telegram (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        autorizacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        chat_externo_id TEXT NOT NULL,
        estado TEXT NOT NULL CHECK(estado IN
          ('iniciada','confirmada','falhou','desconhecida')),
        codigo_resultado TEXT,
        message_id INTEGER,
        retry_after INTEGER,
        iniciada_em TEXT NOT NULL,
        finalizada_em TEXT,
        atualizado_em TEXT NOT NULL,
        CHECK((estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL
          AND retry_after IS NULL AND finalizada_em IS NULL) OR
         (estado='confirmada' AND codigo_resultado='aceita' AND message_id>0
          AND retry_after IS NULL AND finalizada_em IS NOT NULL) OR
         (estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL
          AND finalizada_em IS NOT NULL) OR
         (estado='desconhecida' AND codigo_resultado='sem_confirmacao'
          AND message_id IS NULL AND retry_after IS NULL AND finalizada_em IS NOT NULL)),
        CHECK(retry_after IS NULL OR (codigo_resultado='limite_telegram'
          AND retry_after BETWEEN 1 AND 86400)),
        FOREIGN KEY(autorizacao_id) REFERENCES campanha_autorizacoes_envio_telegram(id)
          ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT)""")
    _recusar(
        "autorizacao_envio_telegram_valida",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c ON c.id=p.campanha_id "
        "JOIN contas_envio a ON a.id=NEW.conta_envio_id JOIN inscricoes_conta i "
        "ON i.id=NEW.inscricao_id JOIN identidades_telegram t ON t.id=NEW.identidade_telegram_id "
        "JOIN eventos_telegram e ON e.id=NEW.evento_telegram_id "
        "WHERE p.id=NEW.preparacao_id AND p.campanha_id=NEW.campanha_id "
        "AND p.confirmado_em IS NOT NULL AND p.invalidado_em IS NULL "
        "AND p.impressao=NEW.impressao AND c.status='preparada' "
        "AND c.conta_envio_id=NEW.conta_envio_id AND a.versao=NEW.conta_versao "
        "AND a.telegram_bot_id=NEW.bot_externo_id AND t.chat_externo_id=NEW.chat_externo_id "
        "AND i.conta_envio_id=NEW.conta_envio_id AND i.identidade_telegram_id=t.id "
        "AND i.estado='iniciada' AND e.inscricao_id=i.id AND e.conta_envio_id=a.id "
        "AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria "
        "AND e.tipo='autorizou' AND e.confirmado=1)",
        "Autorizacao Telegram incompativel",
    )
    for action in ("UPDATE", "DELETE"):
        _recusar(
            f"autorizacao_envio_telegram_sem_{action.lower()}",
            "campanha_autorizacoes_envio_telegram",
            f"BEFORE {action}",
        )
    _recusar(
        "autorizacao_envio_telegram_sem_replace",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        "EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_telegram "
        "WHERE preparacao_id=NEW.preparacao_id OR operacao_id=NEW.operacao_id)",
        "Registro existente",
    )
    _recusar(
        "tentativa_envio_telegram_fixa",
        "campanha_tentativas_envio_telegram",
        "BEFORE UPDATE",
        "OLD.estado!='iniciada' OR NEW.id IS NOT OLD.id OR "
        "NEW.autorizacao_id IS NOT OLD.autorizacao_id OR "
        "NEW.conta_envio_id IS NOT OLD.conta_envio_id OR "
        "NEW.chat_externo_id IS NOT OLD.chat_externo_id OR "
        "NEW.iniciada_em IS NOT OLD.iniciada_em",
        "Tentativa Telegram imutavel",
    )
    _recusar(
        "tentativa_envio_telegram_sem_delete",
        "campanha_tentativas_envio_telegram",
        "BEFORE DELETE",
    )
    _recusar(
        "preparacao_com_envio_sem_invalidar",
        "campanha_preparacoes",
        "BEFORE UPDATE OF invalidado_em",
        "NEW.invalidado_em IS NOT OLD.invalidado_em AND EXISTS(SELECT 1 FROM "
        "campanha_autorizacoes_envio_telegram a WHERE a.preparacao_id=OLD.id)",
        "Envio Telegram preserva preparacao",
    )


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_telegram) OR "
        "EXISTS(SELECT 1 FROM campanha_tentativas_envio_telegram)"
    ).scalar():
        raise RuntimeError("Downgrade recusado: preserve autorizacoes e tentativas Telegram.")
    for name in (
        "autorizacao_envio_telegram_valida",
        "autorizacao_envio_telegram_sem_update",
        "autorizacao_envio_telegram_sem_delete",
        "autorizacao_envio_telegram_sem_replace",
        "tentativa_envio_telegram_fixa",
        "tentativa_envio_telegram_sem_delete",
        "preparacao_com_envio_sem_invalidar",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_table("campanha_tentativas_envio_telegram")
    op.drop_index(
        "ix_autorizacoes_envio_telegram_campanha",
        table_name="campanha_autorizacoes_envio_telegram",
    )
    op.drop_table("campanha_autorizacoes_envio_telegram")
    op.drop_index("uq_preparacao_telegram_vinculo", table_name="campanha_preparacao_itens_telegram")
