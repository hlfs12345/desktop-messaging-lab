"""Contas mock sem credenciais; reconstrucao localizada com FKs ativas."""

from alembic import op

revision = "0008_contas_mock"
down_revision = "0007_preparacoes_cifradas"
branch_labels = None
depends_on = None


def reconstruir(mock):
    conn = op.get_bind()
    if not conn.in_transaction() or conn.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
        raise RuntimeError("Migracao exige transacao e chaves estrangeiras ativas.")
    original = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='contas_envio'"
    ).scalar_one()
    replacements = (
        ("('qr_candidato','meta_cloud')", "('qr_candidato','meta_cloud','mock')"),
        ("provedor!='qr_candidato'", "provedor NOT IN ('qr_candidato','mock')"),
    )
    sql = original
    for old, new in replacements:
        source, target = (old, new) if mock else (new, old)
        if sql.count(source) != 1:
            raise RuntimeError("Esquema de contas incompativel; preserve o banco.")
        sql = sql.replace(source, target)
    extras = (
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_schema WHERE tbl_name='contas_envio' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name"
        )
        .scalars()
        .all()
    )
    # Nao desligar FKs nem zerar defer_foreign_keys antes do COMMIT: isso apagaria
    # pendencias. Reinserir os pais DEPOIS de recriar o nome original resolve-as.
    conn.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    if conn.exec_driver_sql("PRAGMA defer_foreign_keys").scalar() != 1:
        raise RuntimeError("SQLite nao suporta adiamento de chaves estrangeiras.")
    conn.exec_driver_sql("CREATE TEMP TABLE contas_copia_0008 AS SELECT * FROM contas_envio")
    conn.exec_driver_sql("DROP TABLE contas_envio")
    conn.exec_driver_sql(sql)
    conn.exec_driver_sql("INSERT INTO contas_envio SELECT * FROM contas_copia_0008")
    for ddl in extras:
        conn.exec_driver_sql(ddl)
    conn.exec_driver_sql("DROP TABLE contas_copia_0008")
    if conn.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("Migracao violaria referencias; preserve o banco.")


def upgrade():
    reconstruir(True)


def downgrade():
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM contas_envio WHERE provedor='mock' LIMIT 1")
        .first()
        is not None
    ):
        raise RuntimeError("Downgrade recusado: preserve contas mock e historico.")
    reconstruir(False)
