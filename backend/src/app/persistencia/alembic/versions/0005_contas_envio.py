"""Cadastro local de contas; nenhuma credencial dentro do SQLite."""

from alembic import op

revision = "0005_contas_envio"
down_revision = "0004_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE contas_envio (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        nome TEXT NOT NULL CHECK(length(trim(nome)) BETWEEN 1 AND 120),
        nome_busca TEXT NOT NULL CHECK(length(nome_busca) BETWEEN 1 AND 240),
        plataforma TEXT NOT NULL,
        provedor TEXT NOT NULL CHECK(
            (plataforma='whatsapp' AND provedor IN ('qr_candidato','meta_cloud')) OR
            (plataforma='telegram' AND provedor='telegram_bot_api')),
        ativo INTEGER NOT NULL CHECK(ativo IN (0,1)),
        removido_em TEXT CHECK(removido_em IS NULL OR
            (ativo=0 AND segredo_ref IS NULL AND removido_em>=criado_em)),
        segredo_ref TEXT CHECK(segredo_ref IS NULL OR
            (length(segredo_ref)=32 AND segredo_ref NOT GLOB '*[^0-9a-f]*'
            AND provedor!='qr_candidato')),
        versao INTEGER NOT NULL CHECK(versao>=1),
        criado_por_usuario_id TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL CHECK(atualizado_em>=criado_em),
        CONSTRAINT fk_contas_envio_criado_por_usuario_id_usuarios
            FOREIGN KEY(criado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_contas_envio_catalogo ON contas_envio(ativo,nome_busca,id)")
    op.execute("CREATE INDEX ix_contas_envio_provedor ON contas_envio(provedor,removido_em)")
    op.execute("""CREATE TRIGGER contas_envio_identidade_fixa BEFORE UPDATE ON contas_envio
        WHEN NEW.id!=OLD.id OR NEW.plataforma!=OLD.plataforma OR NEW.provedor!=OLD.provedor
        OR NEW.criado_em!=OLD.criado_em OR NEW.criado_por_usuario_id!=OLD.criado_por_usuario_id
        OR OLD.removido_em IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'Identidade imutavel ou conta removida'); END""")
    op.execute("""CREATE TRIGGER contas_envio_sem_substituicao BEFORE INSERT ON contas_envio
        WHEN EXISTS (SELECT 1 FROM contas_envio WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'Conta existente'); END""")
    op.execute("""CREATE TRIGGER contas_envio_sem_delete BEFORE DELETE ON contas_envio
        BEGIN SELECT RAISE(ABORT, 'Use remocao logica'); END""")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM contas_envio").scalar():
        raise RuntimeError("Downgrade recusado: preserve contas e histórico.")
    for name in ("identidade_fixa", "sem_substituicao", "sem_delete"):
        op.execute("DROP TRIGGER contas_envio_" + name)
    op.drop_table("contas_envio")
