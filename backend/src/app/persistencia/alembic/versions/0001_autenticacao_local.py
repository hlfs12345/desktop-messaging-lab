"""Autenticação local e marcador permanente de primeira configuração."""

import sqlalchemy as sa
from alembic import op

revision = "0001_autenticacao_local"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("login_normalizado", sa.Text(), nullable=False),
        sa.Column("senha_hash", sa.Text(), nullable=False),
        sa.Column("perfil", sa.Text(), nullable=False),
        sa.Column("ativo", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("criado_em", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usuarios"),
        sa.CheckConstraint("length(id) = 36", name="ck_usuarios_id_uuid"),
        sa.CheckConstraint("length(nome) BETWEEN 1 AND 120", name="ck_usuarios_nome_tamanho"),
        sa.CheckConstraint("length(login) BETWEEN 3 AND 64", name="ck_usuarios_login_tamanho"),
        sa.CheckConstraint(
            "length(login_normalizado) BETWEEN 3 AND 64",
            name="ck_usuarios_login_normalizado_tamanho",
        ),
        sa.CheckConstraint("perfil IN ('administrador', 'operador')", name="ck_usuarios_perfil"),
        sa.CheckConstraint("ativo IN (0, 1)", name="ck_usuarios_ativo"),
        sa.CheckConstraint("atualizado_em >= criado_em", name="ck_usuarios_datas"),
    )
    op.create_index("uq_usuarios_login_normalizado", "usuarios", ["login_normalizado"], unique=True)
    op.create_table(
        "sessoes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("usuario_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.Text(), nullable=False),
        sa.Column("expira_em", sa.Text(), nullable=False),
        sa.Column("revogado_em", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessoes"),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            ondelete="RESTRICT",
            name="fk_sessoes_usuario_id_usuarios",
        ),
        sa.CheckConstraint("length(id) = 36", name="ck_sessoes_id_uuid"),
        sa.CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'", name="ck_sessoes_hash"
        ),
        sa.CheckConstraint("expira_em > criado_em", name="ck_sessoes_expiracao"),
        sa.CheckConstraint(
            "revogado_em IS NULL OR revogado_em >= criado_em", name="ck_sessoes_revogacao"
        ),
    )
    op.create_index("uq_sessoes_token_hash", "sessoes", ["token_hash"], unique=True)
    op.create_index("ix_sessoes_usuario_expiracao", "sessoes", ["usuario_id", "expira_em"])
    op.create_table(
        "estado_autenticacao",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("configurado_em", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_estado_autenticacao"),
        sa.CheckConstraint(
            "id = '00000000-0000-0000-0000-000000000001'", name="ck_estado_autenticacao_unico"
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO estado_autenticacao (id) VALUES ('00000000-0000-0000-0000-000000000001')"
        )
    )


def downgrade() -> None:
    # Exclusivamente em base descartável: remove credenciais e estado inicial.
    op.drop_table("estado_autenticacao")
    op.drop_index("ix_sessoes_usuario_expiracao", table_name="sessoes")
    op.drop_index("uq_sessoes_token_hash", table_name="sessoes")
    op.drop_table("sessoes")
    op.drop_index("uq_usuarios_login_normalizado", table_name="usuarios")
    op.drop_table("usuarios")
