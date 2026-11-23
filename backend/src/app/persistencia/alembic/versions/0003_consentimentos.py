"""Consentimento local, identidades e historico imutavel."""

from alembic import op

revision = "0003_consentimentos"
down_revision = "0002_contatos_listas_importacoes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE contatos ADD COLUMN bloqueio_global_em TEXT")
    op.execute(
        "ALTER TABLE contatos ADD COLUMN consentimento_versao INTEGER"
        " NOT NULL DEFAULT 0 CHECK (consentimento_versao>=0)"
    )
    op.execute("CREATE UNIQUE INDEX uq_telefones_id_contato ON telefones_contato (id,contato_id)")
    op.execute(
        "\nCREATE TABLE escopos_comunicacao (\n\tid TEXT NOT NULL, \n\tnom"
        "e TEXT NOT NULL, \n\tsingleton INTEGER NOT NULL, \n\tcriado_por_"
        "usuario_id TEXT NOT NULL, \n\tcriado_em TEXT NOT NULL, \n\tCONST"
        "RAINT pk_escopos_comunicacao PRIMARY KEY (id), \n\tCONSTRAINT "
        "ck_escopos_comunicacao_uuid CHECK (length(id)=36), \n\tCONSTRA"
        "INT ck_escopos_comunicacao_nome CHECK (length(trim(nome)) BE"
        "TWEEN 1 AND 120), \n\tCONSTRAINT ck_escopos_comunicacao_single"
        "ton CHECK (singleton=1), \n\tCONSTRAINT uq_escopos_comunicacao"
        "_singleton UNIQUE (singleton), \n\tCONSTRAINT fk_escopos_comun"
        "icacao_criado_por_usuario_id_usuarios FOREIGN KEY(criado_por"
        "_usuario_id) REFERENCES usuarios (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE identidades_contato (\n\tid TEXT NOT NULL, \n\tcon"
        "tato_id TEXT NOT NULL, \n\ttelefone_id TEXT NOT NULL, \n\tcanal "
        "TEXT NOT NULL, \n\tbloqueio_canal_em TEXT, \n\tcriado_por_usuari"
        "o_id TEXT NOT NULL, \n\tcriado_em TEXT NOT NULL, \n\tCONSTRAINT "
        "pk_identidades_contato PRIMARY KEY (id), \n\tCONSTRAINT ck_ide"
        "ntidades_contato_uuid CHECK (length(id)=36), \n\tCONSTRAINT ck"
        "_identidades_contato_canal CHECK (canal='whatsapp'), \n\tCONST"
        "RAINT fk_identidades_contato_telefone_id_telefones_contato F"
        "OREIGN KEY(telefone_id, contato_id) REFERENCES telefones_con"
        "tato (id, contato_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_id"
        "entidades_contato_telefone_id UNIQUE (telefone_id, canal), \n"
        "\tCONSTRAINT uq_identidades_contato_id UNIQUE (id, contato_id"
        "), \n\tCONSTRAINT fk_identidades_contato_contato_id_contatos F"
        "OREIGN KEY(contato_id) REFERENCES contatos (id) ON DELETE RE"
        "STRICT, \n\tCONSTRAINT fk_identidades_contato_criado_por_usuar"
        "io_id_usuarios FOREIGN KEY(criado_por_usuario_id) REFERENCES"
        " usuarios (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute("CREATE INDEX ix_identidades_contato ON identidades_contato (contato_id, canal)")
    op.execute(
        "\nCREATE TABLE eventos_consentimento (\n\tid TEXT NOT NULL, \n\te"
        "scopo_id TEXT NOT NULL, \n\tcontato_id TEXT NOT NULL, \n\tidenti"
        "dade_contato_id TEXT, \n\tcategoria TEXT, \n\ttipo TEXT NOT NULL"
        ", \n\torigem TEXT NOT NULL, \n\tocorrido_em TEXT NOT NULL, \n\treg"
        "istrado_em TEXT NOT NULL, \n\tmetodo_captura TEXT, \n\tevidencia"
        "_referencia TEXT, \n\tjustificativa TEXT, \n\tusuario_id TEXT NO"
        "T NULL, \n\trequisicao_hash TEXT NOT NULL, \n\tversao_resultante"
        " INTEGER NOT NULL, \n\tCONSTRAINT pk_eventos_consentimento PRI"
        "MARY KEY (id), \n\tCONSTRAINT ck_eventos_consentimento_identif"
        "icadores CHECK (length(id)=36 AND length(requisicao_hash)=64"
        "), \n\tCONSTRAINT ck_eventos_consentimento_origem_versao CHECK"
        " (origem='operador' AND versao_resultante>=1), \n\tCONSTRAINT "
        "ck_eventos_consentimento_datas CHECK (ocorrido_em <= registr"
        "ado_em), \n\tCONSTRAINT ck_eventos_consentimento_alvo CHECK (("
        "tipo IN ('autorizou','revogou') AND identidade_contato_id IS"
        " NOT NULL AND categoria IS NOT NULL AND categoria IN ('promo"
        "coes','atualizacoes_solicitadas','eventos_grupos')) OR (tipo"
        " IN ('bloqueio_canal','desbloqueio_canal') AND identidade_co"
        "ntato_id IS NOT NULL AND categoria IS NULL) OR (tipo IN ('bl"
        "oqueio_global','desbloqueio_global') AND identidade_contato_"
        "id IS NULL AND categoria IS NULL)), \n\tCONSTRAINT ck_eventos_"
        "consentimento_evidencia CHECK (tipo!='autorizou' OR (metodo_"
        "captura IS NOT NULL AND length(trim(metodo_captura)) BETWEEN"
        " 1 AND 120 AND evidencia_referencia IS NOT NULL AND length(t"
        "rim(evidencia_referencia)) BETWEEN 1 AND 500)), \n\tCONSTRAINT"
        " ck_eventos_consentimento_justificativa CHECK (tipo='autoriz"
        "ou' OR (justificativa IS NOT NULL AND length(trim(justificat"
        "iva)) BETWEEN 1 AND 500)), \n\tCONSTRAINT ck_eventos_consentim"
        "ento_tamanhos CHECK ((metodo_captura IS NULL OR length(metod"
        "o_captura)<=120) AND (evidencia_referencia IS NULL OR length"
        "(evidencia_referencia)<=500) AND (justificativa IS NULL OR l"
        "ength(justificativa)<=500)), \n\tCONSTRAINT fk_eventos_consent"
        "imento_identidade_contato_id_identidades_contato FOREIGN KEY"
        "(identidade_contato_id, contato_id) REFERENCES identidades_c"
        "ontato (id, contato_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_"
        "eventos_consentimento_id UNIQUE (id, escopo_id), \n\tCONSTRAIN"
        "T fk_eventos_consentimento_escopo_id_escopos_comunicacao FOR"
        "EIGN KEY(escopo_id) REFERENCES escopos_comunicacao (id) ON D"
        "ELETE RESTRICT, \n\tCONSTRAINT fk_eventos_consentimento_contat"
        "o_id_contatos FOREIGN KEY(contato_id) REFERENCES contatos (i"
        "d) ON DELETE RESTRICT, \n\tCONSTRAINT fk_eventos_consentimento"
        "_usuario_id_usuarios FOREIGN KEY(usuario_id) REFERENCES usua"
        "rios (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_eventos_consentimento_historico ON eventos_c"
        "onsentimento (contato_id, registrado_em, id)"
    )
    op.execute(
        "\nCREATE TABLE preferencias_contato (\n\tescopo_id TEXT NOT NUL"
        "L, \n\tidentidade_contato_id TEXT NOT NULL, \n\tcategoria TEXT N"
        "OT NULL, \n\testado TEXT NOT NULL, \n\tevento_origem_id TEXT NOT"
        " NULL, \n\tatualizado_em TEXT NOT NULL, \n\tCONSTRAINT pk_prefer"
        "encias_contato PRIMARY KEY (escopo_id, identidade_contato_id"
        ", categoria), \n\tCONSTRAINT ck_preferencias_contato_categoria"
        " CHECK (categoria IN ('promocoes','atualizacoes_solicitadas'"
        ",'eventos_grupos')), \n\tCONSTRAINT ck_preferencias_contato_es"
        "tado CHECK (estado IN ('desconhecido','autorizado','bloquead"
        "o')), \n\tCONSTRAINT fk_preferencias_contato_evento_origem_id_"
        "eventos_consentimento FOREIGN KEY(evento_origem_id, escopo_i"
        "d) REFERENCES eventos_consentimento (id, escopo_id) ON DELET"
        "E RESTRICT, \n\tCONSTRAINT fk_preferencias_contato_escopo_id_e"
        "scopos_comunicacao FOREIGN KEY(escopo_id) REFERENCES escopos"
        "_comunicacao (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_prefer"
        "encias_contato_identidade_contato_id_identidades_contato FOR"
        "EIGN KEY(identidade_contato_id) REFERENCES identidades_conta"
        "to (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_preferencias_filtro ON preferencias_contato "
        "(escopo_id, categoria, estado, identidade_contato_id)"
    )
    op.execute(
        "CREATE TRIGGER eventos_consentimento_sem_update BEFORE UPDAT"
        "E ON eventos_consentimento BEGIN SELECT RAISE(ABORT, 'Histor"
        "ico imutavel'); END"
    )
    op.execute(
        "CREATE TRIGGER eventos_consentimento_sem_delete BEFORE DELET"
        "E ON eventos_consentimento BEGIN SELECT RAISE(ABORT, 'Histor"
        "ico imutavel'); END"
    )


def downgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT count(*) FROM eventos_consentimento").scalar():
        raise RuntimeError("Downgrade recusado: preserve o historico de consentimento.")
    op.execute("DROP TRIGGER eventos_consentimento_sem_update")
    op.execute("DROP TRIGGER eventos_consentimento_sem_delete")
    op.drop_table("preferencias_contato")
    op.drop_table("eventos_consentimento")
    op.drop_table("identidades_contato")
    op.drop_table("escopos_comunicacao")
    op.drop_index("uq_telefones_id_contato", table_name="telefones_contato")
    op.execute("ALTER TABLE contatos DROP COLUMN consentimento_versao")
    op.execute("ALTER TABLE contatos DROP COLUMN bloqueio_global_em")
