"""Rascunhos e seleção, sem mensagens ou conteúdo preparado."""

from alembic import op

revision = "0006_campanhas_rascunho"
down_revision = "0005_contas_envio"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE campanhas (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        nome TEXT NOT NULL CHECK(length(trim(nome)) BETWEEN 1 AND 120),
        nome_busca TEXT NOT NULL CHECK(length(nome_busca) BETWEEN 1 AND 240),
        conta_envio_id TEXT NOT NULL,
        escopo_id TEXT NOT NULL,
        template_versao_id TEXT NOT NULL,
        categoria TEXT NOT NULL CHECK(categoria IN
            ('promocoes','atualizacoes_solicitadas','eventos_grupos')),
        status TEXT NOT NULL CHECK(status='rascunho'),
        versao INTEGER NOT NULL CHECK(versao>=1),
        criado_por_usuario_id TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL CHECK(atualizado_em>=criado_em),
        CONSTRAINT fk_campanhas_conta_envio_id_contas_envio FOREIGN KEY(conta_envio_id)
            REFERENCES contas_envio(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanhas_escopo_id_escopos_comunicacao FOREIGN KEY(escopo_id) REFERENCES
            escopos_comunicacao(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanhas_template_versao_id_template_versoes FOREIGN
            KEY(template_versao_id) REFERENCES template_versoes(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanhas_criado_por_usuario_id_usuarios FOREIGN
            KEY(criado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_campanhas_catalogo ON campanhas(nome_busca,id)")
    op.execute("CREATE INDEX ix_campanhas_conta ON campanhas(conta_envio_id,criado_em)")
    op.execute("""CREATE TABLE campanha_listas (
        campanha_id TEXT NOT NULL,
        lista_id TEXT NOT NULL,
        PRIMARY KEY(campanha_id,lista_id),
        CONSTRAINT fk_campanha_listas_campanha_id_campanhas FOREIGN KEY(campanha_id) REFERENCES
            campanhas(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_listas_lista_id_listas FOREIGN KEY(lista_id) REFERENCES
            listas(id) ON DELETE RESTRICT
    )""")
    op.execute("""CREATE TABLE campanha_selecoes (
        campanha_id TEXT NOT NULL,
        identidade_contato_id TEXT NOT NULL,
        selecionado_em TEXT NOT NULL,
        PRIMARY KEY(campanha_id,identidade_contato_id),
        CONSTRAINT fk_campanha_selecoes_campanha_id_campanhas FOREIGN KEY(campanha_id)
            REFERENCES campanhas(id) ON DELETE RESTRICT,
        CONSTRAINT fk_campanha_selecoes_identidade_contato_id_identidades_contato FOREIGN
            KEY(identidade_contato_id) REFERENCES identidades_contato(id) ON DELETE RESTRICT
    )""")
    for action in ("INSERT", "UPDATE"):
        op.execute(f"""CREATE TRIGGER campanhas_compativel_{action.lower()}
            BEFORE {action} ON campanhas
            WHEN NOT EXISTS (
                SELECT 1 FROM contas_envio c
                JOIN template_versoes v ON v.id=NEW.template_versao_id
                JOIN templates t ON t.id=v.template_id
                WHERE c.id=NEW.conta_envio_id AND c.plataforma=v.plataforma
                AND t.categoria=NEW.categoria)
            BEGIN SELECT RAISE(ABORT, 'Referencias incompativeis'); END""")
    op.execute("""CREATE TRIGGER campanhas_identidade_fixa BEFORE UPDATE ON campanhas
        WHEN NEW.id!=OLD.id OR NEW.criado_em!=OLD.criado_em
        OR NEW.criado_por_usuario_id!=OLD.criado_por_usuario_id
        BEGIN SELECT RAISE(ABORT, 'Identidade imutavel'); END""")
    op.execute("""CREATE TRIGGER campanhas_sem_substituicao BEFORE INSERT ON campanhas
        WHEN EXISTS (SELECT 1 FROM campanhas WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'Campanha existente'); END""")
    op.execute("""CREATE TRIGGER campanhas_sem_delete BEFORE DELETE ON campanhas
        BEGIN SELECT RAISE(ABORT, 'Preserve o historico'); END""")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM campanhas").scalar():
        raise RuntimeError("Downgrade recusado: preserve campanhas e histórico.")
    op.drop_table("campanha_selecoes")
    op.drop_table("campanha_listas")
    for name in (
        "compativel_insert",
        "compativel_update",
        "identidade_fixa",
        "sem_substituicao",
        "sem_delete",
    ):
        op.execute("DROP TRIGGER campanhas_" + name)
    op.drop_table("campanhas")
