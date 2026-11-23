"""Cadastro independente, listas, importações e auditoria mínima."""

from alembic import op

revision = "0002_contatos_listas_importacoes"
down_revision = "0001_autenticacao_local"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE contatos (\n\tnome TEXT, \n\tnome_busca TEXT DEFAULT '' NOT "
        "NULL, \n\tobservacoes TEXT, \n\tid TEXT NOT NULL, \n\tativo INTEGER DEFAULT "
        "'1' NOT NULL, \n\tversao INTEGER DEFAULT '1' NOT NULL, \n\tcriado_em TEXT "
        "NOT NULL, \n\tatualizado_em TEXT NOT NULL, \n\tCONSTRAINT pk_contatos PRIM"
        "ARY KEY (id), \n\tCONSTRAINT ck_contatos_id_uuid CHECK (length(id)=36), "
        "\n\tCONSTRAINT ck_contatos_ativo CHECK (ativo IN (0,1)), \n\tCONSTRAINT ck"
        "_contatos_versao CHECK (versao >= 1), \n\tCONSTRAINT ck_contatos_datas C"
        "HECK (atualizado_em >= criado_em), \n\tCONSTRAINT ck_contatos_nome CHECK"
        " (nome IS NULL OR length(nome) BETWEEN 1 AND 120), \n\tCONSTRAINT ck_con"
        "tatos_observacoes CHECK (observacoes IS NULL OR length(observacoes)<=2"
        "000)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_contatos_busca ON contatos (ativo, nome_busca, id)")
    op.execute(
        "\nCREATE TABLE listas (\n\tnome TEXT NOT NULL, \n\tnome_normalizado TEXT NO"
        "T NULL, \n\tdescricao TEXT, \n\tid TEXT NOT NULL, \n\tativo INTEGER DEFAULT "
        "'1' NOT NULL, \n\tversao INTEGER DEFAULT '1' NOT NULL, \n\tcriado_em TEXT "
        "NOT NULL, \n\tatualizado_em TEXT NOT NULL, \n\tCONSTRAINT pk_listas PRIMAR"
        "Y KEY (id), \n\tCONSTRAINT ck_listas_id_uuid CHECK (length(id)=36), \n\tCO"
        "NSTRAINT ck_listas_ativo CHECK (ativo IN (0,1)), \n\tCONSTRAINT ck_lista"
        "s_versao CHECK (versao >= 1), \n\tCONSTRAINT ck_listas_datas CHECK (atua"
        "lizado_em >= criado_em), \n\tCONSTRAINT ck_listas_nome CHECK (length(nom"
        "e) BETWEEN 1 AND 120), \n\tCONSTRAINT ck_listas_nome_normalizado CHECK ("
        "length(nome_normalizado) BETWEEN 1 AND 240), \n\tCONSTRAINT ck_listas_de"
        "scricao CHECK (descricao IS NULL OR length(descricao)<=2000)\n)\n\n"
    )
    op.execute("CREATE UNIQUE INDEX uq_listas_nome_normalizado ON listas (nome_normalizado)")
    op.execute(
        "\nCREATE TABLE auditoria (\n\tid TEXT NOT NULL, \n\tusuario_id TEXT, \n\tacao"
        " TEXT NOT NULL, \n\tentidade TEXT NOT NULL, \n\tentidade_id TEXT NOT NULL,"
        " \n\tresumo_json TEXT NOT NULL, \n\tocorrido_em TEXT NOT NULL, \n\tCONSTRAIN"
        "T pk_auditoria PRIMARY KEY (id), \n\tCONSTRAINT fk_auditoria_usuario_id_"
        "usuarios FOREIGN KEY(usuario_id) REFERENCES usuarios (id) ON DELETE RE"
        "STRICT\n)\n\n"
    )
    op.execute("CREATE INDEX ix_auditoria_data ON auditoria (ocorrido_em)")
    op.execute("CREATE INDEX ix_auditoria_entidade ON auditoria (entidade, entidade_id)")
    op.execute(
        "\nCREATE TABLE importacoes (\n\tid TEXT NOT NULL, \n\tusuario_id TEXT NOT N"
        "ULL, \n\tnome_arquivo TEXT NOT NULL, \n\thash_arquivo TEXT, \n\tstatus TEXT "
        "NOT NULL, \n\tconfiguracao_json TEXT DEFAULT '{}' NOT NULL, \n\tnormalizac"
        "ao_versao TEXT NOT NULL, \n\tcontagens_json TEXT DEFAULT '{}' NOT NULL, "
        "\n\tcriado_em TEXT NOT NULL, \n\tconcluido_em TEXT, \n\tCONSTRAINT pk_import"
        "acoes PRIMARY KEY (id), \n\tCONSTRAINT ck_importacoes_id_uuid CHECK (len"
        "gth(id)=36), \n\tCONSTRAINT ck_importacoes_arquivo CHECK (length(nome_ar"
        "quivo) BETWEEN 1 AND 180), \n\tCONSTRAINT ck_importacoes_hash CHECK (has"
        "h_arquivo IS NULL OR (length(hash_arquivo)=64 AND hash_arquivo NOT GLO"
        "B '*[^0-9a-f]*')), \n\tCONSTRAINT ck_importacoes_status CHECK (status IN"
        " ('criada','carregada','validando','pronta','confirmada','cancelada','"
        "interrompida','falhou','expirada')), \n\tCONSTRAINT fk_importacoes_usuar"
        "io_id_usuarios FOREIGN KEY(usuario_id) REFERENCES usuarios (id) ON DEL"
        "ETE RESTRICT\n)\n\n"
    )
    op.execute("CREATE INDEX ix_importacoes_data ON importacoes (criado_em, id)")
    op.execute("CREATE INDEX ix_importacoes_usuario ON importacoes (usuario_id, status)")
    op.execute(
        "\nCREATE TABLE lista_contatos (\n\tlista_id TEXT NOT NULL, \n\tcontato_id T"
        "EXT NOT NULL, \n\tadicionado_em TEXT NOT NULL, \n\tCONSTRAINT pk_lista_con"
        "tatos PRIMARY KEY (lista_id, contato_id), \n\tCONSTRAINT fk_lista_contat"
        "os_lista_id_listas FOREIGN KEY(lista_id) REFERENCES listas (id) ON DEL"
        "ETE RESTRICT, \n\tCONSTRAINT fk_lista_contatos_contato_id_contatos FOREI"
        "GN KEY(contato_id) REFERENCES contatos (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute("CREATE INDEX ix_lista_contatos_contato ON lista_contatos (contato_id, lista_id)")
    op.execute(
        "\nCREATE TABLE telefones_contato (\n\tcontato_id TEXT NOT NULL, \n\ttelefon"
        "e_e164 TEXT NOT NULL, \n\tid TEXT NOT NULL, \n\tativo INTEGER DEFAULT '1' "
        "NOT NULL, \n\tversao INTEGER DEFAULT '1' NOT NULL, \n\tcriado_em TEXT NOT "
        "NULL, \n\tatualizado_em TEXT NOT NULL, \n\tCONSTRAINT pk_telefones_contato"
        " PRIMARY KEY (id), \n\tCONSTRAINT ck_telefones_contato_id_uuid CHECK (le"
        "ngth(id)=36), \n\tCONSTRAINT ck_telefones_contato_ativo CHECK (ativo IN "
        "(0,1)), \n\tCONSTRAINT ck_telefones_contato_versao CHECK (versao >= 1), "
        "\n\tCONSTRAINT ck_telefones_contato_datas CHECK (atualizado_em >= criado"
        "_em), \n\tCONSTRAINT ck_telefones_contato_e164 CHECK (length(telefone_e1"
        "64) BETWEEN 3 AND 16 AND substr(telefone_e164,1,1)='+' AND substr(tele"
        "fone_e164,2,1) BETWEEN '1' AND '9' AND substr(telefone_e164,2) NOT GLO"
        "B '*[^0-9]*'), \n\tCONSTRAINT fk_telefones_contato_contato_id_contatos F"
        "OREIGN KEY(contato_id) REFERENCES contatos (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute("CREATE INDEX ix_telefones_contato ON telefones_contato (contato_id, ativo)")
    op.execute("CREATE UNIQUE INDEX uq_telefones_e164 ON telefones_contato (telefone_e164)")
    op.execute(
        "\nCREATE TABLE importacao_linhas (\n\tid TEXT NOT NULL, \n\timportacao_id T"
        "EXT NOT NULL, \n\tnumero_linha INTEGER NOT NULL, \n\tresultado TEXT NOT NU"
        "LL, \n\tmotivo TEXT NOT NULL, \n\tcontato_id TEXT, \n\ttelefone_id TEXT, \n\tC"
        "ONSTRAINT pk_importacao_linhas PRIMARY KEY (id), \n\tCONSTRAINT ck_impor"
        "tacao_linhas_linha CHECK (numero_linha>=1), \n\tCONSTRAINT ck_importacao"
        "_linhas_resultado CHECK (resultado IN ('inserido','atualizado','sem_al"
        "teracao','duplicado','invalido','ignorado')), \n\tCONSTRAINT fk_importac"
        "ao_linhas_importacao_id_importacoes FOREIGN KEY(importacao_id) REFEREN"
        "CES importacoes (id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_importacao_li"
        "nhas_contato_id_contatos FOREIGN KEY(contato_id) REFERENCES contatos ("
        "id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_importacao_linhas_telefone_id_"
        "telefones_contato FOREIGN KEY(telefone_id) REFERENCES telefones_contat"
        "o (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_importacao_resultado ON importacao_linhas (importacao_"
        "id, resultado, numero_linha)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_importacao_linha ON importacao_linhas (importacao_id, numero_linha)"
    )


def downgrade():
    # Somente em base descartável; perde dados de negócio.
    op.drop_table("importacao_linhas")
    op.drop_table("telefones_contato")
    op.drop_table("lista_contatos")
    op.drop_table("importacoes")
    op.drop_table("auditoria")
    op.drop_table("listas")
    op.drop_table("contatos")
