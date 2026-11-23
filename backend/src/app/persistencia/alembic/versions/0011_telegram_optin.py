"""Persistencia Telegram aditiva; preserva os dados WhatsApp."""

from alembic import op

revision = "0011_telegram_optin"
down_revision = "0010_autorizacoes_utc"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE contas_envio ADD COLUMN telegram_bot_id TEXT
        CHECK (telegram_bot_id IS NULL OR (provedor='telegram_bot_api'
          AND length(telegram_bot_id) BETWEEN 1 AND 16
          AND substr(telegram_bot_id,1,1) BETWEEN '1' AND '9'
          AND telegram_bot_id NOT GLOB '*[^0-9]*'))
    """)
    op.execute("CREATE UNIQUE INDEX uq_contas_telegram_bot ON contas_envio(telegram_bot_id)")
    op.execute(
        """CREATE TABLE identidades_telegram (
	id TEXT NOT NULL,
	contato_id TEXT NOT NULL,
	usuario_externo_id TEXT NOT NULL,
	chat_externo_id TEXT NOT NULL,
	criado_por_usuario_id TEXT NOT NULL,
	criado_em TEXT NOT NULL,
	CONSTRAINT pk_identidades_telegram PRIMARY KEY (id),
	CONSTRAINT ck_identidades_telegram_uuid CHECK (length(id)=36),
        CONSTRAINT ck_identidades_telegram_chat_privado CHECK
        (usuario_externo_id=chat_externo_id AND length(usuario_externo_id) BETWEEN 1 AND 16 AND
        substr(usuario_externo_id,1,1) BETWEEN '1' AND '9' AND usuario_externo_id NOT GLOB
        '*[^0-9]*'),
        CONSTRAINT fk_identidades_telegram_contato_id_contatos FOREIGN KEY(contato_id)
        REFERENCES contatos (id) ON DELETE RESTRICT,
	CONSTRAINT uq_identidades_telegram_usuario_externo_id UNIQUE (usuario_externo_id),
	CONSTRAINT uq_identidades_telegram_chat_externo_id UNIQUE (chat_externo_id),
        CONSTRAINT fk_identidades_telegram_criado_por_usuario_id_usuarios FOREIGN
        KEY(criado_por_usuario_id) REFERENCES usuarios (id) ON DELETE RESTRICT
)""".replace("\n", " ")
    )
    op.execute("""CREATE INDEX
        ix_identidades_telegram_contato
        ON identidades_telegram (contato_id)""")
    op.execute(
        """CREATE TABLE inscricoes_conta (
	id TEXT NOT NULL,
	conta_envio_id TEXT NOT NULL,
	identidade_telegram_id TEXT NOT NULL,
	estado TEXT NOT NULL,
	registrado_em TEXT NOT NULL,
	atualizado_em TEXT NOT NULL,
	CONSTRAINT pk_inscricoes_conta PRIMARY KEY (id),
	CONSTRAINT ck_inscricoes_conta_uuid CHECK (length(id)=36),
	CONSTRAINT ck_inscricoes_conta_estado CHECK (estado IN ('iniciada','revogada')),
	CONSTRAINT ck_inscricoes_conta_datas CHECK (atualizado_em>=registrado_em),
	CONSTRAINT uq_inscricoes_conta_conta_envio_id UNIQUE (conta_envio_id, identidade_telegram_id),
	CONSTRAINT uq_inscricoes_conta_id UNIQUE (id, conta_envio_id),
        CONSTRAINT fk_inscricoes_conta_conta_envio_id_contas_envio FOREIGN KEY(conta_envio_id)
        REFERENCES contas_envio (id) ON DELETE RESTRICT,
        CONSTRAINT fk_inscricoes_conta_identidade_telegram_id_identidades_telegram FOREIGN
        KEY(identidade_telegram_id) REFERENCES identidades_telegram (id) ON DELETE RESTRICT
)""".replace("\n", " ")
    )
    op.execute(
        """CREATE TABLE eventos_telegram (
	id TEXT NOT NULL,
	inscricao_id TEXT NOT NULL,
	conta_envio_id TEXT NOT NULL,
	escopo_id TEXT NOT NULL,
	evento_externo_id TEXT NOT NULL,
	tipo TEXT NOT NULL,
	categoria TEXT,
	aviso_versao TEXT,
	aviso_texto TEXT,
	confirmado INTEGER NOT NULL,
	ocorrido_em TEXT NOT NULL,
	registrado_em TEXT NOT NULL,
	usuario_id TEXT NOT NULL,
	requisicao_hash TEXT NOT NULL,
	CONSTRAINT pk_eventos_telegram PRIMARY KEY (id),
	CONSTRAINT ck_eventos_telegram_ids CHECK (length(id)=36 AND length(requisicao_hash)=64),
        CONSTRAINT ck_eventos_telegram_externo CHECK (length(trim(evento_externo_id)) BETWEEN 1
        AND 100),
	CONSTRAINT ck_eventos_telegram_datas CHECK (ocorrido_em<=registrado_em),
        CONSTRAINT ck_eventos_telegram_finalidade CHECK ((tipo='autorizou' AND categoria IN
        ('promocoes','atualizacoes_solicitadas','eventos_grupos') AND categoria IS NOT NULL AND
        confirmado=1 AND aviso_versao IS NOT NULL AND aviso_texto IS NOT NULL AND
        length(trim(aviso_versao)) BETWEEN 1 AND 80 AND length(trim(aviso_texto)) BETWEEN 1 AND
        2000) OR (tipo IN ('iniciou','revogou') AND categoria IS NULL AND confirmado=0 AND
        aviso_versao IS NULL AND aviso_texto IS NULL)),
        CONSTRAINT fk_eventos_telegram_inscricao_id_inscricoes_conta FOREIGN KEY(inscricao_id,
        conta_envio_id) REFERENCES inscricoes_conta (id, conta_envio_id) ON DELETE RESTRICT,
	CONSTRAINT uq_eventos_telegram_conta_envio_id UNIQUE (conta_envio_id, evento_externo_id),
        CONSTRAINT fk_eventos_telegram_escopo_id_escopos_comunicacao FOREIGN KEY(escopo_id)
        REFERENCES escopos_comunicacao (id) ON DELETE RESTRICT,
        CONSTRAINT fk_eventos_telegram_usuario_id_usuarios FOREIGN KEY(usuario_id) REFERENCES
        usuarios (id) ON DELETE RESTRICT
)""".replace("\n", " ")
    )
    op.execute("""CREATE INDEX
        ix_eventos_telegram_aviso
        ON eventos_telegram (conta_envio_id, categoria, aviso_versao)""")
    op.execute("""CREATE INDEX
        ix_eventos_telegram_finalidade
        ON eventos_telegram (inscricao_id, escopo_id, categoria)""")
    for name, body in TRIGGERS.items():
        op.execute(f"CREATE TRIGGER {name} {body}")


TRIGGERS = {
    "contas_bot_preservado": """
        BEFORE UPDATE OF telegram_bot_id ON contas_envio
        WHEN OLD.telegram_bot_id IS NOT NULL AND NEW.telegram_bot_id IS NOT OLD.telegram_bot_id
        BEGIN SELECT RAISE(ABORT, 'Bot vinculado imutavel'); END
    """,
    "inscricoes_conta_chaves": """
        BEFORE UPDATE OF id,conta_envio_id,identidade_telegram_id,registrado_em ON
        inscricoes_conta
        BEGIN SELECT RAISE(ABORT, 'Vinculo imutavel'); END
    """,
    "inscricoes_conta_revogacao": """
        BEFORE UPDATE OF estado ON inscricoes_conta
        WHEN OLD.estado='revogada' AND NEW.estado!='revogada'
        BEGIN SELECT RAISE(ABORT, 'Revogacao preservada'); END
    """,
    "inscricoes_conta_bot": """
        BEFORE INSERT ON inscricoes_conta WHEN NOT EXISTS (
            SELECT 1 FROM contas_envio WHERE id=NEW.conta_envio_id
            AND provedor='telegram_bot_api' AND telegram_bot_id IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'Conta Telegram nao vinculada'); END
    """,
    "eventos_telegram_aceite": """
        BEFORE INSERT ON eventos_telegram WHEN NEW.tipo='autorizou' AND (
            EXISTS (SELECT 1 FROM inscricoes_conta i
                JOIN identidades_telegram t ON t.id=i.identidade_telegram_id
                JOIN contatos c ON c.id=t.contato_id
                WHERE i.id=NEW.inscricao_id
                AND (i.estado='revogada' OR c.bloqueio_global_em IS NOT NULL))
            OR EXISTS (SELECT 1 FROM eventos_telegram e
                WHERE e.conta_envio_id=NEW.conta_envio_id AND e.categoria=NEW.categoria
                AND e.aviso_versao=NEW.aviso_versao AND e.aviso_texto!=NEW.aviso_texto))
        BEGIN SELECT RAISE(ABORT, 'Aceite invalido'); END
    """,
    "eventos_telegram_revogacao": """
        AFTER INSERT ON eventos_telegram WHEN NEW.tipo='revogou'
        BEGIN UPDATE inscricoes_conta SET estado='revogada', atualizado_em=NEW.registrado_em
            WHERE id=NEW.inscricao_id; END
    """,
    "identidades_telegram_sem_update": """
        BEFORE UPDATE ON identidades_telegram
        BEGIN SELECT RAISE(ABORT, 'Historico imutavel'); END
    """,
    "identidades_telegram_sem_delete": """
        BEFORE DELETE ON identidades_telegram
        BEGIN SELECT RAISE(ABORT, 'Historico imutavel'); END
    """,
    "eventos_telegram_sem_update": """
        BEFORE UPDATE ON eventos_telegram
        BEGIN SELECT RAISE(ABORT, 'Historico imutavel'); END
    """,
    "eventos_telegram_sem_delete": """
        BEFORE DELETE ON eventos_telegram
        BEGIN SELECT RAISE(ABORT, 'Historico imutavel'); END
    """,
    "inscricoes_conta_sem_delete": """
        BEFORE DELETE ON inscricoes_conta
        BEGIN SELECT RAISE(ABORT, 'Historico imutavel'); END
    """,
}


def downgrade():
    conn = op.get_bind()
    for table in ("identidades_telegram", "inscricoes_conta", "eventos_telegram"):
        if conn.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first() is not None:
            raise RuntimeError("Downgrade recusado: preserve o historico Telegram.")
    if (
        conn.exec_driver_sql(
            "SELECT 1 FROM contas_envio WHERE telegram_bot_id IS NOT NULL LIMIT 1"
        ).first()
        is not None
    ):
        raise RuntimeError("Downgrade recusado: preserve o vinculo do bot.")
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER {name}")
    op.drop_table("eventos_telegram")
    op.drop_table("inscricoes_conta")
    op.drop_table("identidades_telegram")
    op.drop_index("uq_contas_telegram_bot", table_name="contas_envio")
    op.execute("ALTER TABLE contas_envio DROP COLUMN telegram_bot_id")
