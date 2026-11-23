"""Cursor e recibos de recepção, sem conteúdo de mensagens."""

from alembic import op

revision = "0012_telegram_recepcao"
down_revision = "0011_telegram_optin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE contas_envio ADD COLUMN telegram_proximo_offset INTEGER
        CHECK (telegram_proximo_offset IS NULL OR (telegram_bot_id IS NOT NULL
        AND typeof(telegram_proximo_offset)='integer' AND telegram_proximo_offset>=0))""")
    op.execute(
        """CREATE TABLE telegram_recibos (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        conta_envio_id TEXT NOT NULL,
        update_id INTEGER NOT NULL CHECK(typeof(update_id)='integer' AND update_id>=0),
        resultado TEXT NOT NULL CHECK(resultado IN ('iniciou','revogou','ignorada')),
        registrado_em TEXT NOT NULL,
        CONSTRAINT fk_telegram_recibos_conta_envio_id_contas_envio
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT,
        CONSTRAINT uq_telegram_recibos_conta_envio_id UNIQUE(conta_envio_id,update_id))""".replace(
            "\n", " "
        )
    )
    op.execute(
        "CREATE INDEX ix_telegram_recibos_data ON telegram_recibos(conta_envio_id,registrado_em)"
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER telegram_recibos_sem_{action.lower()}
            BEFORE {action} ON telegram_recibos
            BEGIN SELECT RAISE(ABORT, 'Recibo imutavel'); END""")


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("SELECT 1 FROM telegram_recibos LIMIT 1").first() is not None:
        raise RuntimeError("Downgrade recusado: preserve os recibos Telegram.")
    if (
        conn.exec_driver_sql(
            "SELECT 1 FROM contas_envio WHERE telegram_proximo_offset IS NOT NULL LIMIT 1"
        ).first()
        is not None
    ):
        raise RuntimeError("Downgrade recusado: preserve o cursor Telegram.")
    op.execute("DROP TRIGGER telegram_recibos_sem_update")
    op.execute("DROP TRIGGER telegram_recibos_sem_delete")
    op.drop_table("telegram_recibos")
    op.execute("ALTER TABLE contas_envio DROP COLUMN telegram_proximo_offset")
