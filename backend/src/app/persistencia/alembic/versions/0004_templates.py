"""Catálogo local; versões imutáveis, sem contas ou conectores."""

from alembic import op

revision = "0004_templates"
down_revision = "0003_consentimentos"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE templates (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        nome TEXT NOT NULL CHECK(length(trim(nome)) BETWEEN 1 AND 120),
        nome_busca TEXT NOT NULL CHECK(length(nome_busca) BETWEEN 1 AND 240),
        descricao TEXT CHECK(descricao IS NULL OR length(descricao)<=2000),
        categoria TEXT NOT NULL CHECK(categoria IN
            ('promocoes','atualizacoes_solicitadas','eventos_grupos')),
        ativo INTEGER NOT NULL CHECK(ativo IN (0,1)),
        versao INTEGER NOT NULL CHECK(versao>=1),
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL CHECK(atualizado_em>=criado_em)
    )""")
    op.execute("""CREATE INDEX ix_templates_catalogo
        ON templates(ativo,categoria,nome_busca,id)""")
    op.execute("""CREATE TABLE template_versoes (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        template_id TEXT NOT NULL,
        plataforma TEXT NOT NULL CHECK(plataforma IN ('whatsapp','telegram')),
        numero_versao INTEGER NOT NULL CHECK(numero_versao>=1),
        conteudo TEXT NOT NULL CHECK(length(trim(conteudo)) BETWEEN 1 AND 4000),
        variaveis_json TEXT NOT NULL CHECK(variaveis_json IN ('[]','["nome"]')),
        criado_por_usuario_id TEXT NOT NULL,
        criado_em TEXT NOT NULL,
        CONSTRAINT uq_template_versoes_template_id UNIQUE(template_id,plataforma,numero_versao),
        CONSTRAINT fk_template_versoes_template_id_templates
            FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE RESTRICT,
        CONSTRAINT fk_template_versoes_criado_por_usuario_id_usuarios
            FOREIGN KEY(criado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT
    )""")
    op.execute("""CREATE INDEX ix_template_versoes_plataforma
        ON template_versoes(plataforma,template_id)""")
    for action in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER template_versoes_sem_{action.lower()}
            BEFORE {action} ON template_versoes BEGIN
            SELECT RAISE(ABORT, 'Versao imutavel'); END""")
    # BEFORE INSERT cobre inclusive INSERT OR REPLACE, sem depender de recursive_triggers.
    op.execute("""CREATE TRIGGER template_versoes_sem_substituicao
        BEFORE INSERT ON template_versoes WHEN EXISTS (
            SELECT 1 FROM template_versoes WHERE id=NEW.id OR
            (template_id=NEW.template_id AND plataforma=NEW.plataforma
                AND numero_versao=NEW.numero_versao))
        BEGIN SELECT RAISE(ABORT, 'Versao imutavel'); END""")
    op.execute("""CREATE TRIGGER templates_sem_substituicao
        BEFORE INSERT ON templates WHEN EXISTS (SELECT 1 FROM templates WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'Template existente'); END""")
    op.execute("""CREATE TRIGGER templates_categoria_fixa
        BEFORE UPDATE OF categoria ON templates WHEN NEW.categoria != OLD.categoria
        BEGIN SELECT RAISE(ABORT, 'Categoria imutavel'); END""")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT count(*) FROM templates").scalar():
        raise RuntimeError("Downgrade recusado: preserve templates e versões.")
    op.execute("DROP TRIGGER templates_sem_substituicao")
    op.execute("DROP TRIGGER template_versoes_sem_substituicao")
    op.execute("DROP TRIGGER templates_categoria_fixa")
    op.execute("DROP TRIGGER template_versoes_sem_update")
    op.execute("DROP TRIGGER template_versoes_sem_delete")
    op.drop_table("template_versoes")
    op.drop_table("templates")
