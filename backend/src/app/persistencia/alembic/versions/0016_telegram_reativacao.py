"""Reativacao Telegram explicita, sem apagar o historico de /stop."""

from alembic import op

revision = "0016_telegram_reativacao"
down_revision = "0015_telegram_envio_unitario"
branch_labels = None
depends_on = None


REATIVACAO_VALIDA = """
EXISTS (SELECT 1 FROM eventos_telegram r
    WHERE r.inscricao_id=NEW.inscricao_id
    AND r.escopo_id=NEW.escopo_id
    AND r.tipo='iniciou'
    AND r.evento_externo_id LIKE
        '%:reativacao:aviso:' || NEW.categoria || ':' || NEW.aviso_versao
    AND r.ocorrido_em<=NEW.ocorrido_em
    AND r.registrado_em>COALESCE((SELECT MAX(v.registrado_em)
        FROM eventos_telegram v
        WHERE v.inscricao_id=NEW.inscricao_id AND v.tipo='revogou'),''))
"""


def _gatilho_aceite_reativavel():
    op.execute(f"""CREATE TRIGGER eventos_telegram_aceite
        BEFORE INSERT ON eventos_telegram WHEN NEW.tipo='autorizou' AND (
            EXISTS (SELECT 1 FROM inscricoes_conta i
                JOIN identidades_telegram t ON t.id=i.identidade_telegram_id
                JOIN contatos c ON c.id=t.contato_id
                WHERE i.id=NEW.inscricao_id
                AND (c.bloqueio_global_em IS NOT NULL OR
                    (i.estado='revogada' AND NOT ({REATIVACAO_VALIDA}))))
            OR EXISTS (SELECT 1 FROM eventos_telegram e
                WHERE e.conta_envio_id=NEW.conta_envio_id
                AND e.categoria=NEW.categoria
                AND e.aviso_versao=NEW.aviso_versao
                AND e.aviso_texto!=NEW.aviso_texto))
        BEGIN SELECT RAISE(ABORT, 'Aceite invalido'); END""")


def _gatilho_inscricao_reativavel():
    reativacao = REATIVACAO_VALIDA.replace("NEW.", "a.")
    op.execute(f"""CREATE TRIGGER inscricoes_conta_revogacao
        BEFORE UPDATE OF estado ON inscricoes_conta
        WHEN OLD.estado='revogada' AND NEW.estado!='revogada' AND (
            NEW.estado!='iniciada' OR NOT EXISTS (
                SELECT 1 FROM eventos_telegram a
                WHERE a.inscricao_id=OLD.id AND a.tipo='autorizou'
                AND a.registrado_em=NEW.atualizado_em
                AND ({reativacao})))
        BEGIN SELECT RAISE(ABORT, 'Revogacao preservada'); END""")


def _gatilho_reabrir():
    op.execute(f"""CREATE TRIGGER eventos_telegram_reabre
        AFTER INSERT ON eventos_telegram WHEN NEW.tipo='autorizou'
        AND EXISTS (SELECT 1 FROM inscricoes_conta i
            WHERE i.id=NEW.inscricao_id AND i.estado='revogada')
        AND ({REATIVACAO_VALIDA})
        BEGIN UPDATE inscricoes_conta
            SET estado='iniciada', atualizado_em=NEW.registrado_em
            WHERE id=NEW.inscricao_id; END""")


def _gatilho_autorizacao_envio(*, revogacao=True):
    vigente = "" if not revogacao else """
        AND NOT EXISTS (SELECT 1 FROM eventos_telegram r
            WHERE r.inscricao_id=e.inscricao_id AND r.tipo='revogou'
            AND r.registrado_em>=e.registrado_em)"""
    op.execute(f"""CREATE TRIGGER autorizacao_envio_telegram_valida
        BEFORE INSERT ON campanha_autorizacoes_envio_telegram
        WHEN NOT EXISTS(SELECT 1 FROM campanha_preparacoes p
            JOIN campanhas c ON c.id=p.campanha_id
            JOIN contas_envio a ON a.id=NEW.conta_envio_id
            JOIN inscricoes_conta i ON i.id=NEW.inscricao_id
            JOIN identidades_telegram t ON t.id=NEW.identidade_telegram_id
            JOIN eventos_telegram e ON e.id=NEW.evento_telegram_id
            WHERE p.id=NEW.preparacao_id AND p.campanha_id=NEW.campanha_id
            AND p.confirmado_em IS NOT NULL AND p.invalidado_em IS NULL
            AND p.impressao=NEW.impressao AND c.status='preparada'
            AND c.conta_envio_id=NEW.conta_envio_id AND a.versao=NEW.conta_versao
            AND a.telegram_bot_id=NEW.bot_externo_id
            AND t.chat_externo_id=NEW.chat_externo_id
            AND i.conta_envio_id=NEW.conta_envio_id
            AND i.identidade_telegram_id=t.id AND i.estado='iniciada'
            AND e.inscricao_id=i.id AND e.conta_envio_id=a.id
            AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria
            AND e.tipo='autorizou' AND e.confirmado=1{vigente})
        BEGIN SELECT RAISE(ABORT,'Autorizacao Telegram incompativel'); END""")


def upgrade():
    for name in (
        "inscricoes_conta_revogacao",
        "eventos_telegram_aceite",
        "autorizacao_envio_telegram_valida",
    ):
        op.execute(f"DROP TRIGGER {name}")
    _gatilho_aceite_reativavel()
    _gatilho_inscricao_reativavel()
    _gatilho_reabrir()
    _gatilho_autorizacao_envio()


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("""SELECT 1 FROM eventos_telegram a
        WHERE a.tipo='autorizou' AND EXISTS (
            SELECT 1 FROM eventos_telegram v
            WHERE v.inscricao_id=a.inscricao_id AND v.tipo='revogou'
            AND v.registrado_em<a.registrado_em) LIMIT 1""").first() is not None:
        raise RuntimeError("Downgrade recusado: preserve as reativacoes Telegram.")
    for name in (
        "eventos_telegram_reabre",
        "inscricoes_conta_revogacao",
        "eventos_telegram_aceite",
        "autorizacao_envio_telegram_valida",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.execute("""CREATE TRIGGER inscricoes_conta_revogacao
        BEFORE UPDATE OF estado ON inscricoes_conta
        WHEN OLD.estado='revogada' AND NEW.estado!='revogada'
        BEGIN SELECT RAISE(ABORT, 'Revogacao preservada'); END""")
    op.execute("""CREATE TRIGGER eventos_telegram_aceite
        BEFORE INSERT ON eventos_telegram WHEN NEW.tipo='autorizou' AND (
            EXISTS (SELECT 1 FROM inscricoes_conta i
                JOIN identidades_telegram t ON t.id=i.identidade_telegram_id
                JOIN contatos c ON c.id=t.contato_id
                WHERE i.id=NEW.inscricao_id
                AND (i.estado='revogada' OR c.bloqueio_global_em IS NOT NULL))
            OR EXISTS (SELECT 1 FROM eventos_telegram e
                WHERE e.conta_envio_id=NEW.conta_envio_id
                AND e.categoria=NEW.categoria
                AND e.aviso_versao=NEW.aviso_versao
                AND e.aviso_texto!=NEW.aviso_texto))
        BEGIN SELECT RAISE(ABORT, 'Aceite invalido'); END""")
    _gatilho_autorizacao_envio(revogacao=False)
