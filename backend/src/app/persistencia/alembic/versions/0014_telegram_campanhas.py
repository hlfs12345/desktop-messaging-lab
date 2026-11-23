"""Destino Telegram único em campanhas e preparações cifradas."""

from alembic import op

revision = "0014_telegram_campanhas"
down_revision = "0013_telegram_consentimento"
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
        "CREATE UNIQUE INDEX ix_inscricoes_id_identidade "
        "ON inscricoes_conta(id,identidade_telegram_id)"
    )
    op.execute("""CREATE TABLE campanha_selecoes_telegram (
        campanha_id TEXT NOT NULL PRIMARY KEY CHECK(length(campanha_id)=36),
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        selecionado_em TEXT NOT NULL,
        CONSTRAINT fk_selecao_telegram_campanha FOREIGN KEY(campanha_id)
            REFERENCES campanhas(id) ON DELETE RESTRICT,
        CONSTRAINT fk_selecao_telegram_inscricao_conta
            FOREIGN KEY(inscricao_id,conta_envio_id)
            REFERENCES inscricoes_conta(id,conta_envio_id) ON DELETE RESTRICT,
        CONSTRAINT fk_selecao_telegram_inscricao_identidade
            FOREIGN KEY(inscricao_id,identidade_telegram_id)
            REFERENCES inscricoes_conta(id,identidade_telegram_id) ON DELETE RESTRICT)""")
    op.execute("""CREATE TABLE campanha_preparacao_itens_telegram (
        preparacao_id TEXT NOT NULL PRIMARY KEY,
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        evento_telegram_id TEXT NOT NULL,
        nonce BLOB NOT NULL CHECK(length(nonce)=12),
        conteudo_cifrado BLOB NOT NULL,
        bytes_texto INTEGER NOT NULL CHECK(bytes_texto BETWEEN 1 AND 65536
            AND length(conteudo_cifrado)=bytes_texto+16),
        CONSTRAINT fk_preparacao_telegram_preparacao FOREIGN KEY(preparacao_id)
            REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
        CONSTRAINT fk_preparacao_telegram_identidade FOREIGN KEY(identidade_telegram_id)
            REFERENCES identidades_telegram(id) ON DELETE RESTRICT,
        CONSTRAINT fk_preparacao_telegram_inscricao_conta
            FOREIGN KEY(inscricao_id,conta_envio_id)
            REFERENCES inscricoes_conta(id,conta_envio_id) ON DELETE RESTRICT,
        CONSTRAINT fk_preparacao_telegram_inscricao_identidade
            FOREIGN KEY(inscricao_id,identidade_telegram_id)
            REFERENCES inscricoes_conta(id,identidade_telegram_id) ON DELETE RESTRICT,
        CONSTRAINT fk_preparacao_telegram_evento FOREIGN KEY(evento_telegram_id)
            REFERENCES eventos_telegram(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE INDEX ix_preparacao_telegram_identidade ON "
        "campanha_preparacao_itens_telegram(identidade_telegram_id,preparacao_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_preparacao_telegram_nonce ON "
        "campanha_preparacao_itens_telegram(preparacao_id,nonce)"
    )
    _recusar(
        "selecao_telegram_compativel_insert",
        "campanha_selecoes_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanhas c JOIN contas_envio a "
        "ON a.id=c.conta_envio_id JOIN inscricoes_conta i "
        "ON i.id=NEW.inscricao_id WHERE c.id=NEW.campanha_id "
        "AND c.status='rascunho' AND c.conta_envio_id=NEW.conta_envio_id "
        "AND a.plataforma='telegram' AND a.provedor='telegram_bot_api' "
        "AND i.conta_envio_id=NEW.conta_envio_id "
        "AND i.identidade_telegram_id=NEW.identidade_telegram_id)",
        "Destino Telegram incompativel",
    )
    _recusar(
        "campanha_selecao_telegram_conta_fixa",
        "campanhas",
        "BEFORE UPDATE OF conta_envio_id",
        "NEW.conta_envio_id IS NOT OLD.conta_envio_id AND EXISTS(SELECT 1 FROM "
        "campanha_selecoes_telegram s WHERE s.campanha_id=OLD.id)",
        "Remova o destino Telegram antes de trocar a conta",
    )
    for action in ("INSERT", "UPDATE", "DELETE"):
        row = "OLD" if action == "DELETE" else "NEW"
        _recusar(
            f"campanha_selecoes_telegram_congelada_{action.lower()}",
            "campanha_selecoes_telegram",
            f"BEFORE {action}",
            f"EXISTS(SELECT 1 FROM campanhas WHERE id={row}.campanha_id AND status='preparada')",
            "Selecao congelada",
        )
    _recusar(
        "preparacao_telegram_origem",
        "campanha_preparacao_itens_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c "
        "ON c.id=p.campanha_id JOIN campanha_selecoes_telegram s "
        "ON s.campanha_id=c.id JOIN inscricoes_conta i ON i.id=NEW.inscricao_id "
        "JOIN eventos_telegram e ON e.id=NEW.evento_telegram_id "
        "WHERE p.id=NEW.preparacao_id AND c.conta_envio_id=NEW.conta_envio_id "
        "AND s.conta_envio_id=NEW.conta_envio_id "
        "AND s.identidade_telegram_id=NEW.identidade_telegram_id "
        "AND s.inscricao_id=NEW.inscricao_id AND i.estado='iniciada' "
        "AND e.inscricao_id=NEW.inscricao_id AND e.conta_envio_id=NEW.conta_envio_id "
        "AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria "
        "AND e.tipo='autorizou' AND e.confirmado=1)",
        "Preparacao Telegram incompativel",
    )
    _recusar(
        "campanha_preparacao_itens_telegram_sem_update",
        "campanha_preparacao_itens_telegram",
        "BEFORE UPDATE",
    )
    _recusar(
        "campanha_preparacao_itens_telegram_sem_delete",
        "campanha_preparacao_itens_telegram",
        "BEFORE DELETE",
    )
    _recusar(
        "campanha_preparacao_itens_telegram_sem_replace",
        "campanha_preparacao_itens_telegram",
        "BEFORE INSERT",
        "EXISTS(SELECT 1 FROM campanha_preparacao_itens_telegram "
        "WHERE preparacao_id=NEW.preparacao_id)",
        "Registro existente",
    )


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM campanha_selecoes_telegram) OR "
        "EXISTS(SELECT 1 FROM campanha_preparacao_itens_telegram)"
    ).scalar():
        raise RuntimeError("Downgrade recusado: preserve destinos Telegram e fotografias.")
    for name in (
        "selecao_telegram_compativel_insert",
        "campanha_selecao_telegram_conta_fixa",
        "campanha_selecoes_telegram_congelada_insert",
        "campanha_selecoes_telegram_congelada_update",
        "campanha_selecoes_telegram_congelada_delete",
        "preparacao_telegram_origem",
        "campanha_preparacao_itens_telegram_sem_update",
        "campanha_preparacao_itens_telegram_sem_delete",
        "campanha_preparacao_itens_telegram_sem_replace",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_table("campanha_preparacao_itens_telegram")
    op.drop_table("campanha_selecoes_telegram")
    op.drop_index("ix_inscricoes_id_identidade", table_name="inscricoes_conta")
