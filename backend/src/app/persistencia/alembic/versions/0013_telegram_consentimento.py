"""Recibos de consentimento promocional Telegram."""

from alembic import op

revision = "0013_telegram_consentimento"
down_revision = "0012_telegram_recepcao"
branch_labels = None
depends_on = None


def _criar_recibos(nome, resultados):
    op.execute(
        f"""CREATE TABLE {nome} (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        conta_envio_id TEXT NOT NULL,
        update_id INTEGER NOT NULL CHECK(typeof(update_id)='integer' AND update_id>=0),
        resultado TEXT NOT NULL CHECK(resultado IN ({resultados})),
        registrado_em TEXT NOT NULL,
        CONSTRAINT fk_telegram_recibos_conta_envio_id_contas_envio
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT,
        CONSTRAINT uq_telegram_recibos_conta_envio_id UNIQUE(conta_envio_id,update_id))""".replace(
            "\n", " "
        )
    )


def _gatilhos():
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER telegram_recibos_sem_{action.lower()}
            BEFORE {action} ON telegram_recibos
            BEGIN SELECT RAISE(ABORT, 'Recibo imutavel'); END""")


def _copiar_e_substituir(nome):
    op.execute(
        f"""INSERT INTO {nome}
        (id,conta_envio_id,update_id,resultado,registrado_em)
        SELECT id,conta_envio_id,update_id,resultado,registrado_em FROM telegram_recibos"""
    )
    op.drop_table("telegram_recibos")
    op.rename_table(nome, "telegram_recibos")
    op.execute(
        "CREATE INDEX ix_telegram_recibos_data ON telegram_recibos(conta_envio_id,registrado_em)"
    )
    _gatilhos()


def upgrade():
    op.execute("DROP TRIGGER telegram_recibos_sem_update")
    op.execute("DROP TRIGGER telegram_recibos_sem_delete")
    _criar_recibos("telegram_recibos_novos", "'iniciou','autorizou','revogou','ignorada'")
    _copiar_e_substituir("telegram_recibos_novos")


def downgrade():
    conn = op.get_bind()
    if (
        conn.exec_driver_sql(
            "SELECT 1 FROM telegram_recibos WHERE resultado='autorizou' LIMIT 1"
        ).first()
        is not None
    ):
        raise RuntimeError("Downgrade recusado: preserve os consentimentos Telegram.")
    op.execute("DROP TRIGGER telegram_recibos_sem_update")
    op.execute("DROP TRIGGER telegram_recibos_sem_delete")
    _criar_recibos("telegram_recibos_anteriores", "'iniciou','revogou','ignorada'")
    _copiar_e_substituir("telegram_recibos_anteriores")
