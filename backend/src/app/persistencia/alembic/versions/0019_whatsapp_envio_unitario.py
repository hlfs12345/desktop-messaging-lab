"""Intencao e tentativa imutaveis para um envio WhatsApp."""

from alembic import op

revision = "0019_whatsapp_envio_unitario"
down_revision = "0018_telegram_executor_lote"
branch_labels = None
depends_on = None


def _recusar(nome, tabela, momento, condicao=None, mensagem="Registro imutavel"):
    quando = "" if condicao is None else f" WHEN {condicao}"
    op.execute(
        f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} "
        f"BEGIN SELECT RAISE(ABORT,'{mensagem}'); END"
    )


def upgrade():
    op.execute("""CREATE TABLE campanha_autorizacoes_envio_whatsapp (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        operacao_id TEXT NOT NULL UNIQUE CHECK(length(operacao_id)=36),
        requisicao_hash TEXT NOT NULL CHECK(length(requisicao_hash)=64),
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        identidade_contato_id TEXT NOT NULL,
        evento_consentimento_id TEXT NOT NULL,
        destino_e164 TEXT NOT NULL CHECK(length(destino_e164) BETWEEN 3 AND 16
          AND substr(destino_e164,1,1)='+'
          AND substr(destino_e164,2,1) BETWEEN '1' AND '9'
          AND substr(destino_e164,2) NOT GLOB '*[^0-9]*'),
        conta_versao INTEGER NOT NULL CHECK(conta_versao>=1),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        conteudo_hash TEXT NOT NULL CHECK(length(conteudo_hash)=64),
        autorizado_por_usuario_id TEXT NOT NULL,
        autorizado_em TEXT NOT NULL,
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,identidade_contato_id)
          REFERENCES campanha_preparacao_itens(preparacao_id,identidade_contato_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT,
        FOREIGN KEY(evento_consentimento_id) REFERENCES eventos_consentimento(id)
          ON DELETE RESTRICT,
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE INDEX ix_autorizacoes_envio_whatsapp_campanha ON "
        "campanha_autorizacoes_envio_whatsapp(campanha_id,autorizado_em)"
    )
    op.execute("""CREATE TABLE campanha_tentativas_envio_whatsapp (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        autorizacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        estado TEXT NOT NULL CHECK(estado IN
          ('iniciada','confirmada','falhou','desconhecida')),
        codigo_resultado TEXT,
        message_id TEXT,
        iniciada_em TEXT NOT NULL,
        finalizada_em TEXT,
        atualizado_em TEXT NOT NULL,
        CHECK((estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL
          AND finalizada_em IS NULL) OR
         (estado='confirmada' AND codigo_resultado='aceita'
          AND length(message_id) BETWEEN 1 AND 256 AND finalizada_em IS NOT NULL) OR
         (estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL
          AND finalizada_em IS NOT NULL) OR
         (estado='desconhecida' AND codigo_resultado='sem_confirmacao'
          AND message_id IS NULL AND finalizada_em IS NOT NULL)),
        FOREIGN KEY(autorizacao_id) REFERENCES campanha_autorizacoes_envio_whatsapp(id)
          ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT)""")
    _recusar(
        "autorizacao_envio_whatsapp_valida",
        "campanha_autorizacoes_envio_whatsapp",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanha_preparacoes p "
        "JOIN campanhas c ON c.id=p.campanha_id "
        "JOIN campanha_preparacao_itens pi ON pi.preparacao_id=p.id "
        "AND pi.identidade_contato_id=NEW.identidade_contato_id AND pi.incluido=1 "
        "JOIN contas_envio a ON a.id=NEW.conta_envio_id "
        "JOIN identidades_contato i ON i.id=NEW.identidade_contato_id "
        "JOIN contatos x ON x.id=i.contato_id "
        "JOIN telefones_contato t ON t.id=i.telefone_id AND t.contato_id=x.id "
        "JOIN preferencias_contato pc ON pc.escopo_id=c.escopo_id "
        "AND pc.identidade_contato_id=i.id AND pc.categoria=c.categoria "
        "JOIN eventos_consentimento e ON e.id=NEW.evento_consentimento_id "
        "WHERE p.id=NEW.preparacao_id AND p.campanha_id=NEW.campanha_id "
        "AND p.confirmado_em IS NOT NULL AND p.invalidado_em IS NULL "
        "AND p.impressao=NEW.impressao AND c.status='preparada' "
        "AND c.conta_envio_id=a.id AND a.id=NEW.conta_envio_id "
        "AND a.provedor='qr_candidato' AND a.plataforma='whatsapp' "
        "AND a.ativo=1 AND a.removido_em IS NULL AND a.versao=NEW.conta_versao "
        "AND i.canal='whatsapp' AND i.bloqueio_canal_em IS NULL "
        "AND x.ativo=1 AND x.bloqueio_global_em IS NULL AND t.ativo=1 "
        "AND t.telefone_e164=NEW.destino_e164 AND pc.estado='autorizado' "
        "AND pc.evento_origem_id=e.id AND e.tipo='autorizou' "
        "AND e.escopo_id=c.escopo_id AND e.identidade_contato_id=i.id "
        "AND e.contato_id=x.id AND e.categoria=c.categoria "
        "AND EXISTS(SELECT 1 FROM campanha_listas cl JOIN listas l ON l.id=cl.lista_id "
        "JOIN lista_contatos lc ON lc.lista_id=l.id WHERE cl.campanha_id=c.id "
        "AND lc.contato_id=x.id AND l.ativo=1) "
        "AND NOT EXISTS(SELECT 1 FROM consentimento_suspensoes s "
        "WHERE s.confirmado_em IS NULL AND s.escopo_id=c.escopo_id "
        "AND s.contato_id=x.id AND (s.identidade_contato_id IS NULL "
        "OR s.identidade_contato_id=i.id) AND (s.categoria IS NULL "
        "OR s.categoria=c.categoria)))",
        "Autorizacao WhatsApp incompativel",
    )
    for action in ("UPDATE", "DELETE"):
        _recusar(
            f"autorizacao_envio_whatsapp_sem_{action.lower()}",
            "campanha_autorizacoes_envio_whatsapp",
            f"BEFORE {action}",
        )
    _recusar(
        "autorizacao_envio_whatsapp_sem_replace",
        "campanha_autorizacoes_envio_whatsapp",
        "BEFORE INSERT",
        "EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_whatsapp "
        "WHERE preparacao_id=NEW.preparacao_id OR operacao_id=NEW.operacao_id)",
        "Registro existente",
    )
    _recusar(
        "tentativa_envio_whatsapp_fixa",
        "campanha_tentativas_envio_whatsapp",
        "BEFORE UPDATE",
        "OLD.estado!='iniciada' OR NEW.id IS NOT OLD.id OR "
        "NEW.autorizacao_id IS NOT OLD.autorizacao_id OR "
        "NEW.conta_envio_id IS NOT OLD.conta_envio_id OR "
        "NEW.iniciada_em IS NOT OLD.iniciada_em",
        "Tentativa WhatsApp imutavel",
    )
    _recusar(
        "tentativa_envio_whatsapp_sem_delete",
        "campanha_tentativas_envio_whatsapp",
        "BEFORE DELETE",
    )
    _recusar(
        "preparacao_com_envio_whatsapp_sem_invalidar",
        "campanha_preparacoes",
        "BEFORE UPDATE OF invalidado_em",
        "NEW.invalidado_em IS NOT OLD.invalidado_em AND EXISTS(SELECT 1 FROM "
        "campanha_autorizacoes_envio_whatsapp a WHERE a.preparacao_id=OLD.id)",
        "Envio WhatsApp preserva preparacao",
    )


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_whatsapp) OR "
        "EXISTS(SELECT 1 FROM campanha_tentativas_envio_whatsapp)"
    ).scalar():
        raise RuntimeError("Downgrade recusado: preserve autorizacoes e tentativas WhatsApp.")
    for name in (
        "autorizacao_envio_whatsapp_valida",
        "autorizacao_envio_whatsapp_sem_update",
        "autorizacao_envio_whatsapp_sem_delete",
        "autorizacao_envio_whatsapp_sem_replace",
        "tentativa_envio_whatsapp_fixa",
        "tentativa_envio_whatsapp_sem_delete",
        "preparacao_com_envio_whatsapp_sem_invalidar",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_table("campanha_tentativas_envio_whatsapp")
    op.drop_index(
        "ix_autorizacoes_envio_whatsapp_campanha",
        table_name="campanha_autorizacoes_envio_whatsapp",
    )
    op.drop_table("campanha_autorizacoes_envio_whatsapp")
