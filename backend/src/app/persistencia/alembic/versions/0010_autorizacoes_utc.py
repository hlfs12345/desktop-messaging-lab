"""Entrega 18 A1/A2: compatibilidade da 0009 e persistencia; sem executor."""

# ruff: noqa: E501

import re
from pathlib import Path

from alembic import op

revision = "0010_autorizacoes_utc"
down_revision = "0009_autorizacoes_simulacao"
branch_labels = None
depends_on = None

TABELA = "campanha_autorizacoes_simulacao"


def _data_utc(coluna):
    # Regra congelada aqui: nao depender do ORM nem modificar a revisao 0009.
    return (
        f"(typeof({coluna})='text' AND length({coluna})=27 AND "
        f"length(CAST({coluna} AS BLOB))=27 AND "
        f"{coluna} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
        "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]."
        "[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND "
        f"substr({coluna},1,4) BETWEEN '0001' AND '9999' AND "
        f"date(substr({coluna},1,10),'+0 days') IS substr({coluna},1,10) AND "
        f"substr({coluna},12,2) BETWEEN '00' AND '23' AND "
        f"substr({coluna},15,2) BETWEEN '00' AND '59' AND "
        f"substr({coluna},18,2) BETWEEN '00' AND '59')"
    )


DATAS_ORIGINAIS = """length(autorizado_em)=27 AND
    autorizado_em GLOB '????-??-??T??:??:??.??????Z' AND
    julianday(autorizado_em) IS NOT NULL AND
    (revogado_em IS NULL OR (length(revogado_em)=27 AND
    revogado_em GLOB '????-??-??T??:??:??.??????Z' AND
    julianday(revogado_em) IS NOT NULL AND revogado_em>=autorizado_em))"""
DATAS_CORRIGIDAS = (
    f"{_data_utc('autorizado_em')} AND (revogado_em IS NULL OR "
    f"({_data_utc('revogado_em')} AND revogado_em>=autorizado_em))"
)

# DDL completo e fechado das duas variantes. So a CHECK de datas varia.
# FKs, tipos, defaults, nulabilidade, constraints e opcoes nao sao inferidos por nome.
DDL_TABELA = """CREATE TABLE campanha_autorizacoes_simulacao (
    id TEXT NOT NULL,
    campanha_id TEXT NOT NULL,
    preparacao_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    impressao TEXT NOT NULL,
    resumo_configuracao TEXT NOT NULL,
    autorizado_por_usuario_id TEXT NOT NULL,
    autorizado_em TEXT NOT NULL,
    estado TEXT NOT NULL,
    versao INTEGER NOT NULL,
    revogado_por_usuario_id TEXT,
    revogado_em TEXT,
    motivo_codigo TEXT,
    CONSTRAINT pk_campanha_autorizacoes_simulacao PRIMARY KEY(id),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_uuid CHECK(length(id)=36),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_resumos CHECK(
        length(impressao)=64 AND impressao NOT GLOB '*[^0-9a-f]*' AND
        length(resumo_configuracao)=64 AND resumo_configuracao NOT GLOB '*[^0-9a-f]*'),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_estado CHECK(estado IN ('vigente','revogada')),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_versao CHECK(
        typeof(versao)='integer' AND versao>=1),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_datas CHECK({datas}),
    CONSTRAINT ck_campanha_autorizacoes_simulacao_revogacao CHECK(
        (estado='vigente' AND revogado_por_usuario_id IS NULL AND
        revogado_em IS NULL AND motivo_codigo IS NULL) OR
        (estado='revogada' AND revogado_por_usuario_id IS NOT NULL AND
        revogado_em IS NOT NULL AND motivo_codigo IS NOT NULL AND
        motivo_codigo IN ('revogacao_manual','retorno_rascunho'))),
    CONSTRAINT uq_campanha_autorizacoes_simulacao_preparacao_id UNIQUE(preparacao_id),
    CONSTRAINT fk_campanha_autorizacoes_simulacao_campanha_id_campanhas
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
    CONSTRAINT fk_campanha_autorizacoes_simulacao_preparacao_id_campanha_preparacoes
        FOREIGN KEY(preparacao_id) REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
    CONSTRAINT fk_campanha_autorizacoes_simulacao_base_id_campanha_contexto_criptografico
        FOREIGN KEY(base_id) REFERENCES campanha_contexto_criptografico(base_id) ON DELETE RESTRICT,
    CONSTRAINT fk_campanha_autorizacoes_simulacao_autorizado_por_usuario_id_usuarios
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT,
    CONSTRAINT fk_campanha_autorizacoes_simulacao_revogado_por_usuario_id_usuarios
        FOREIGN KEY(revogado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)"""

OBJETOS_0009 = {
    ("index", "sqlite_autoindex_campanha_autorizacoes_simulacao_1", TABELA): None,
    ("index", "sqlite_autoindex_campanha_autorizacoes_simulacao_2", TABELA): None,
    ("index", "ix_autorizacoes_simulacao_vigente", TABELA): (
        "CREATE UNIQUE INDEX ix_autorizacoes_simulacao_vigente ON "
        "campanha_autorizacoes_simulacao(campanha_id) WHERE estado='vigente'"
    ),
    ("index", "ix_autorizacoes_simulacao_historico", TABELA): (
        "CREATE INDEX ix_autorizacoes_simulacao_historico ON "
        "campanha_autorizacoes_simulacao(campanha_id,autorizado_em,id)"
    ),
    ("trigger", "autorizacoes_simulacao_origem", TABELA): """CREATE TRIGGER
        autorizacoes_simulacao_origem BEFORE INSERT ON campanha_autorizacoes_simulacao
        WHEN NEW.estado!='vigente' OR NEW.versao!=1
        OR NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c
        ON c.id=p.campanha_id WHERE p.id=NEW.preparacao_id AND
        p.campanha_id=NEW.campanha_id AND p.base_id=NEW.base_id AND
        p.impressao=NEW.impressao AND p.confirmado_em IS NOT NULL AND
        p.confirmado_em<=NEW.autorizado_em AND p.invalidado_em IS NULL AND
        c.status='preparada')
        BEGIN SELECT RAISE(ABORT,'Origem da autorizacao incompativel'); END""",
    ("trigger", "autorizacoes_simulacao_sem_replace", TABELA): """CREATE TRIGGER
        autorizacoes_simulacao_sem_replace BEFORE INSERT ON campanha_autorizacoes_simulacao
        WHEN EXISTS(SELECT 1 FROM campanha_autorizacoes_simulacao
        WHERE id=NEW.id OR preparacao_id=NEW.preparacao_id
        OR (campanha_id=NEW.campanha_id AND estado='vigente' AND NEW.estado='vigente'))
        BEGIN SELECT RAISE(ABORT,'Autorizacao existente'); END""",
    ("trigger", "autorizacoes_simulacao_transicao", TABELA): """CREATE TRIGGER
        autorizacoes_simulacao_transicao BEFORE UPDATE ON campanha_autorizacoes_simulacao
        WHEN NEW.id IS NOT OLD.id OR NEW.campanha_id IS NOT OLD.campanha_id
        OR NEW.preparacao_id IS NOT OLD.preparacao_id OR NEW.base_id IS NOT OLD.base_id
        OR NEW.impressao IS NOT OLD.impressao
        OR NEW.resumo_configuracao IS NOT OLD.resumo_configuracao
        OR NEW.autorizado_por_usuario_id IS NOT OLD.autorizado_por_usuario_id
        OR NEW.autorizado_em IS NOT OLD.autorizado_em
        OR OLD.estado!='vigente' OR NEW.estado!='revogada' OR NEW.versao!=OLD.versao+1
        BEGIN SELECT RAISE(ABORT,'Decisao imutavel ou transicao invalida'); END""",
    ("trigger", "autorizacoes_simulacao_sem_delete", TABELA): """CREATE TRIGGER
        autorizacoes_simulacao_sem_delete BEFORE DELETE ON campanha_autorizacoes_simulacao
        BEGIN SELECT RAISE(ABORT,'Preserve o historico de autorizacoes'); END""",
    ("trigger", "preparacao_autorizada_sem_invalidacao", "campanha_preparacoes"): """CREATE TRIGGER
        preparacao_autorizada_sem_invalidacao BEFORE UPDATE OF invalidado_em
        ON campanha_preparacoes WHEN NEW.invalidado_em IS NOT NULL AND
        EXISTS(SELECT 1 FROM campanha_autorizacoes_simulacao
        WHERE preparacao_id=OLD.id AND estado='vigente')
        BEGIN SELECT RAISE(ABORT,'Revogue a autorizacao antes de invalidar'); END""",
}

PROTECOES = {
    f"autorizacoes_simulacao_utc_{evento.lower()}_0010": (
        f"CREATE TRIGGER autorizacoes_simulacao_utc_{evento.lower()}_0010 "
        f"BEFORE {evento} ON {TABELA} WHEN ("
        f"{_data_utc('NEW.autorizado_em')} AND (NEW.revogado_em IS NULL OR "
        f"({_data_utc('NEW.revogado_em')} AND NEW.revogado_em>=NEW.autorizado_em))"
        ") IS NOT 1 BEGIN SELECT RAISE(ABORT,'Datas UTC invalidas na autorizacao'); END"
    )
    for evento in ("INSERT", "UPDATE")
}


def _sql(sql):
    # Comparar tokens, preservando literais, maiusculas e fronteiras de identificadores.
    # Remover whitespace indiscriminadamente confundiria INTEGER com INTE GER (tipo SQLite).
    # Comentarios e variantes semanticamente semelhantes desconhecidas sao recusados.
    if sql is None:
        return None
    return tuple(
        re.findall(
            r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|!=|>=|<=|<>|\|\||[^ \t\r\n\f\v]",
            sql,
        )
    )


def _validar(conn, *, protegida=False, operacional=None, dados=True):
    """Somente leitura e repetivel; reconhecer a fronteira completa de autorizacoes."""
    if (
        not conn.in_transaction()
        or not conn.connection.driver_connection.in_transaction
        or conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1
    ):
        raise RuntimeError("Compatibilidade 0010 exige transacao e integridade referencial.")
    esperados = dict(OBJETOS_0009)
    if operacional is None:
        operacional = protegida
    operacionais = _objetos_operacionais()
    if operacional:
        esperados.update(operacionais)
    if protegida:
        esperados.update({("trigger", nome, TABELA): sql for nome, sql in PROTECOES.items()})
    nomes = {chave[1] for chave in esperados} | {TABELA} | set(PROTECOES)
    nomes.update(chave[1] for chave in operacionais)
    atuais = {}
    # Incluir dependencias externas de toda a fronteira, nao apenas nomes conhecidos.
    fronteira = {TABELA, *TABELAS_OPERACIONAIS}
    referencias = r"\b(?:" + "|".join(sorted(fronteira)) + r")\b"
    for tipo, nome, tabela, sql in conn.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema"
    ):
        referencia = sql and re.search(referencias, sql, re.I)
        if tabela in fronteira or nome in nomes or referencia:
            atuais[tipo, nome, tabela] = _sql(sql)
    ddl = atuais.pop(("table", TABELA, TABELA), None)
    variantes = {
        _sql(DDL_TABELA.format(datas=DATAS_ORIGINAIS)): "original",
        _sql(DDL_TABELA.format(datas=DATAS_CORRIGIDAS)): "corrigida",
    }
    variante = variantes.get(ddl)
    if variante is None or atuais != {chave: _sql(sql) for chave, sql in esperados.items()}:
        raise RuntimeError("Esquema 0009 desconhecido; compatibilidade 0010 recusada.")
    if dados and conn.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
        raise RuntimeError("Integridade referencial invalida; compatibilidade 0010 recusada.")
    if (
        dados
        and conn.exec_driver_sql(
            f"SELECT 1 FROM {TABELA} WHERE ({DATAS_CORRIGIDAS}) IS NOT 1 LIMIT 1"
        ).first()
        is not None
    ):
        # Nao ler pelo ORM, normalizar datas ou revelar dados/IDs do historico.
        raise RuntimeError("Datas historicas invalidas; preserve os dados para revisao.")
    return variante


def upgrade():
    conn = op.get_bind()
    _validar(conn)
    for sql in PROTECOES.values():
        op.execute(sql)
    _validar(conn, protegida=True, operacional=False)
    for sql in TABELAS_OPERACIONAIS.values():
        op.execute(sql)
    for _, sql in INDICES_OPERACIONAIS.values():
        op.execute(sql)
    for _, sql in TRIGGERS_OPERACIONAIS.values():
        op.execute(sql)
    _validar(conn, protegida=True)


def downgrade():
    conn = op.get_bind()
    variante = _validar(conn, protegida=True)
    # A intencao pode preceder o espelho SQLite quando este recusa escrita.
    # Nao permitir que downgrade descarte essa fronteira de recuperacao.
    try:
        for _, nome, arquivo in conn.exec_driver_sql("PRAGMA database_list"):
            if nome == "main" and arquivo:
                diario = Path(arquivo).with_suffix(".optouts")
                if diario.exists() and any(diario.iterdir()):
                    raise RuntimeError("Downgrade recusado: preserve o diario de descadastramento.")
    except OSError:
        raise RuntimeError("Downgrade recusado: diario de descadastramento indisponivel.") from None
    for tabela in TABELAS_OPERACIONAIS:
        if conn.exec_driver_sql(f"SELECT 1 FROM {tabela} LIMIT 1").first() is not None:
            raise RuntimeError("Downgrade recusado: preserve todo o historico operacional.")
    if variante != "corrigida":
        raise RuntimeError("Downgrade recusado: preserve a protecao da variante original da 0009.")
    if conn.exec_driver_sql(f"SELECT 1 FROM {TABELA} LIMIT 1").first() is not None:
        raise RuntimeError("Downgrade recusado: preserve autorizacoes e decisoes historicas.")
    for nome in TRIGGERS_OPERACIONAIS:
        op.execute(f"DROP TRIGGER {nome}")
    for tabela in reversed(TABELAS_OPERACIONAIS):
        op.execute(f"DROP TABLE {tabela}")
    _validar(conn, protegida=True, operacional=False)
    for nome in PROTECOES:
        op.execute(f"DROP TRIGGER {nome}")
    _validar(conn)


# DDL congelado no A2; sem importacao do ORM durante a migracao.
TABELAS_OPERACIONAIS = {
    "campanha_execucoes": "CREATE TABLE campanha_execucoes (\n"
    "\tid TEXT NOT NULL, \n"
    "\tcampanha_id TEXT NOT NULL, \n"
    "\tpreparacao_id TEXT NOT NULL, \n"
    "\tautorizacao_id TEXT NOT NULL, \n"
    "\testado TEXT NOT NULL, \n"
    "\tversao INTEGER NOT NULL, \n"
    "\tproprietario_id TEXT NOT NULL, \n"
    "\tgeracao INTEGER NOT NULL, \n"
    "\tslot INTEGER DEFAULT 1 NOT NULL, \n"
    "\tcriado_por_usuario_id TEXT NOT NULL, \n"
    "\talterado_por_usuario_id TEXT, \n"
    "\tcriado_em TEXT NOT NULL, \n"
    "\tatualizado_em TEXT NOT NULL, \n"
    "\tCONSTRAINT pk_campanha_execucoes PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_campanha_execucoes_id UNIQUE (id, campanha_id, "
    "preparacao_id), \n"
    "\tCONSTRAINT ck_campanha_execucoes_identidade CHECK (length(id)=36 AND "
    "length(proprietario_id)=36 AND typeof(versao)='integer' AND versao>=1 "
    "AND typeof(geracao)='integer' AND geracao>=1 AND slot=1), \n"
    "\tCONSTRAINT ck_campanha_execucoes_estado CHECK (estado IN "
    "('executando','pausando','pausada','concluida','concluida_com_falhas','interrompida')), \n"
    "\tCONSTRAINT ck_campanha_execucoes_datas CHECK ((criado_em IS NULL OR "
    "(typeof(criado_em)='text' AND length(criado_em)=27 AND "
    "length(CAST(criado_em AS BLOB))=27 AND criado_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(criado_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(criado_em,1,10),'+0 days') IS substr(criado_em,1,10) AND "
    "substr(criado_em,12,2) BETWEEN '00' AND '23' AND "
    "substr(criado_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(criado_em,18,2) BETWEEN '00' AND '59')) AND (atualizado_em IS "
    "NULL OR (typeof(atualizado_em)='text' AND length(atualizado_em)=27 AND "
    "length(CAST(atualizado_em AS BLOB))=27 AND atualizado_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(atualizado_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(atualizado_em,1,10),'+0 days') IS "
    "substr(atualizado_em,1,10) AND substr(atualizado_em,12,2) BETWEEN '00' "
    "AND '23' AND substr(atualizado_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(atualizado_em,18,2) BETWEEN '00' AND '59')) AND "
    "atualizado_em>=criado_em), \n"
    "\tCONSTRAINT fk_campanha_execucoes_campanha_id_campanhas FOREIGN "
    "KEY(campanha_id) REFERENCES campanhas (id) ON DELETE RESTRICT, \n"
    "\tCONSTRAINT uq_campanha_execucoes_preparacao_id UNIQUE "
    "(preparacao_id), \n"
    "\tCONSTRAINT fk_campanha_execucoes_preparacao_id_campanha_preparacoes "
    "FOREIGN KEY(preparacao_id) REFERENCES campanha_preparacoes (id) ON "
    "DELETE RESTRICT, \n"
    "\tCONSTRAINT uq_campanha_execucoes_autorizacao_id UNIQUE "
    "(autorizacao_id), \n"
    "\tCONSTRAINT "
    "fk_campanha_execucoes_autorizacao_id_campanha_autorizacoes_simulacao "
    "FOREIGN KEY(autorizacao_id) REFERENCES campanha_autorizacoes_simulacao "
    "(id) ON DELETE RESTRICT, \n"
    "\tCONSTRAINT fk_campanha_execucoes_criado_por_usuario_id_usuarios "
    "FOREIGN KEY(criado_por_usuario_id) REFERENCES usuarios (id) ON DELETE "
    "RESTRICT, \n"
    "\tCONSTRAINT fk_campanha_execucoes_alterado_por_usuario_id_usuarios "
    "FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios (id) ON "
    "DELETE RESTRICT\n"
    ")",
    "campanha_destinatarios": "CREATE TABLE campanha_destinatarios (\n"
    "\tid TEXT NOT NULL, \n"
    "\tcampanha_id TEXT NOT NULL, \n"
    "\tidentidade_contato_id TEXT NOT NULL, \n"
    "\tCONSTRAINT pk_campanha_destinatarios PRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_campanha_destinatarios_uuid CHECK "
    "(length(id)=36), \n"
    "\tCONSTRAINT uq_campanha_destinatarios_campanha_id UNIQUE "
    "(campanha_id, identidade_contato_id), \n"
    "\tCONSTRAINT uq_campanha_destinatarios_id UNIQUE (id, campanha_id, "
    "identidade_contato_id), \n"
    "\tCONSTRAINT fk_campanha_destinatarios_campanha_id_campanhas "
    "FOREIGN KEY(campanha_id) REFERENCES campanhas (id) ON DELETE "
    "RESTRICT, \n"
    "\tCONSTRAINT "
    "fk_campanha_destinatarios_identidade_contato_id_identidades_contato "
    "FOREIGN KEY(identidade_contato_id) REFERENCES identidades_contato "
    "(id) ON DELETE RESTRICT\n"
    ")",
    "campanha_execucao_itens": "CREATE TABLE campanha_execucao_itens (\n"
    "\tid TEXT NOT NULL, \n"
    "\texecucao_id TEXT NOT NULL, \n"
    "\tcampanha_id TEXT NOT NULL, \n"
    "\tpreparacao_id TEXT NOT NULL, \n"
    "\tdestinatario_id TEXT NOT NULL, \n"
    "\tidentidade_contato_id TEXT NOT NULL, \n"
    "\tordem INTEGER NOT NULL, \n"
    "\testado TEXT NOT NULL, \n"
    "\tversao INTEGER NOT NULL, \n"
    "\tmotivo_codigo TEXT, \n"
    "\talterado_por_usuario_id TEXT, \n"
    "\tatualizado_em TEXT NOT NULL, \n"
    "\tCONSTRAINT pk_campanha_execucao_itens PRIMARY KEY (id), \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_itens_execucao_id_campanha_execucoes FOREIGN "
    "KEY(execucao_id, campanha_id, preparacao_id) REFERENCES "
    "campanha_execucoes (id, campanha_id, preparacao_id) ON DELETE "
    "RESTRICT, \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_itens_destinatario_id_campanha_destinatarios "
    "FOREIGN KEY(destinatario_id, campanha_id, identidade_contato_id) "
    "REFERENCES campanha_destinatarios (id, campanha_id, "
    "identidade_contato_id) ON DELETE RESTRICT, \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_itens_preparacao_id_campanha_preparacao_itens "
    "FOREIGN KEY(preparacao_id, identidade_contato_id) REFERENCES "
    "campanha_preparacao_itens (preparacao_id, identidade_contato_id) "
    "ON DELETE RESTRICT, \n"
    "\tCONSTRAINT uq_campanha_execucao_itens_id UNIQUE (id, "
    "execucao_id), \n"
    "\tCONSTRAINT uq_execucao_itens_destinatario UNIQUE (execucao_id, "
    "destinatario_id), \n"
    "\tCONSTRAINT uq_execucao_itens_ordem UNIQUE (execucao_id, "
    "ordem), \n"
    "\tCONSTRAINT ck_campanha_execucao_itens_identidade CHECK "
    "(length(id)=36 AND typeof(ordem)='integer' AND ordem>=1 AND "
    "typeof(versao)='integer' AND versao>=1), \n"
    "\tCONSTRAINT ck_campanha_execucao_itens_estado CHECK (estado IN "
    "('pendente','reservado','processando','aceita_provedor','falhou','desconhecida','ignorado','interrompido')), \n"
    "\tCONSTRAINT ck_campanha_execucao_itens_motivo CHECK "
    "((estado='ignorado' AND motivo_codigo IS NOT NULL AND "
    "motivo_codigo='impedimento_atual') OR (estado='interrompido' AND "
    "motivo_codigo IS NOT NULL AND motivo_codigo='encerramento') OR "
    "(estado NOT IN ('ignorado','interrompido') AND motivo_codigo IS "
    "NULL)), \n"
    "\tCONSTRAINT ck_campanha_execucao_itens_datas CHECK "
    "((atualizado_em IS NULL OR (typeof(atualizado_em)='text' AND "
    "length(atualizado_em)=27 AND length(CAST(atualizado_em AS "
    "BLOB))=27 AND atualizado_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(atualizado_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(atualizado_em,1,10),'+0 days') IS "
    "substr(atualizado_em,1,10) AND substr(atualizado_em,12,2) BETWEEN "
    "'00' AND '23' AND substr(atualizado_em,15,2) BETWEEN '00' AND "
    "'59' AND substr(atualizado_em,18,2) BETWEEN '00' AND '59'))), \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_itens_alterado_por_usuario_id_usuarios "
    "FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios (id) ON "
    "DELETE RESTRICT\n"
    ")",
    "tentativas_envio": "CREATE TABLE tentativas_envio (\n"
    "\tid TEXT NOT NULL, \n"
    "\texecucao_id TEXT NOT NULL, \n"
    "\titem_id TEXT NOT NULL, \n"
    "\tnumero INTEGER NOT NULL, \n"
    "\testado TEXT NOT NULL, \n"
    "\tversao INTEGER NOT NULL, \n"
    "\tproprietario_id TEXT NOT NULL, \n"
    "\tgeracao INTEGER NOT NULL, \n"
    "\tslot INTEGER DEFAULT 1 NOT NULL, \n"
    "\treservada_em TEXT NOT NULL, \n"
    "\tinvocacao_em TEXT, \n"
    "\tfinalizada_em TEXT, \n"
    "\tatualizado_em TEXT NOT NULL, \n"
    "\tcodigo_resultado TEXT, \n"
    "\talterado_por_usuario_id TEXT, \n"
    "\tCONSTRAINT pk_tentativas_envio PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_tentativas_envio_item_id_campanha_execucao_itens FOREIGN "
    "KEY(item_id, execucao_id) REFERENCES campanha_execucao_itens (id, "
    "execucao_id) ON DELETE RESTRICT, \n"
    "\tCONSTRAINT uq_tentativas_envio_id UNIQUE (id, item_id, execucao_id), \n"
    "\tCONSTRAINT uq_tentativas_envio_item_id UNIQUE (item_id, numero), \n"
    "\tCONSTRAINT ck_tentativas_envio_identidade CHECK (length(id)=36 AND "
    "length(proprietario_id)=36 AND typeof(versao)='integer' AND versao>=1 "
    "AND typeof(geracao)='integer' AND geracao>=1 AND "
    "typeof(numero)='integer' AND numero>=1 AND slot=1), \n"
    "\tCONSTRAINT ck_tentativas_envio_resultado CHECK ((estado='reservada' "
    "AND invocacao_em IS NULL AND finalizada_em IS NULL AND codigo_resultado "
    "IS NULL) OR (estado='iniciada' AND invocacao_em IS NOT NULL AND "
    "finalizada_em IS NULL AND codigo_resultado IS NULL) OR "
    "(estado='interrompida' AND invocacao_em IS NULL AND finalizada_em IS NOT "
    "NULL AND codigo_resultado IS NULL) OR (estado IN "
    "('aceita_provedor','falhou','desconhecida') AND invocacao_em IS NOT NULL "
    "AND finalizada_em IS NOT NULL AND codigo_resultado IS NOT NULL AND "
    "((estado='aceita_provedor' AND codigo_resultado='aceita') OR "
    "(estado='falhou' AND codigo_resultado='falha') OR (estado='desconhecida' "
    "AND codigo_resultado='sem_confirmacao')))), \n"
    "\tCONSTRAINT ck_tentativas_envio_datas CHECK ((reservada_em IS NULL OR "
    "(typeof(reservada_em)='text' AND length(reservada_em)=27 AND "
    "length(CAST(reservada_em AS BLOB))=27 AND reservada_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(reservada_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(reservada_em,1,10),'+0 days') IS substr(reservada_em,1,10) "
    "AND substr(reservada_em,12,2) BETWEEN '00' AND '23' AND "
    "substr(reservada_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(reservada_em,18,2) BETWEEN '00' AND '59')) AND (invocacao_em IS "
    "NULL OR (typeof(invocacao_em)='text' AND length(invocacao_em)=27 AND "
    "length(CAST(invocacao_em AS BLOB))=27 AND invocacao_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(invocacao_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(invocacao_em,1,10),'+0 days') IS substr(invocacao_em,1,10) "
    "AND substr(invocacao_em,12,2) BETWEEN '00' AND '23' AND "
    "substr(invocacao_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(invocacao_em,18,2) BETWEEN '00' AND '59')) AND (finalizada_em IS "
    "NULL OR (typeof(finalizada_em)='text' AND length(finalizada_em)=27 AND "
    "length(CAST(finalizada_em AS BLOB))=27 AND finalizada_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(finalizada_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(finalizada_em,1,10),'+0 days') IS substr(finalizada_em,1,10) "
    "AND substr(finalizada_em,12,2) BETWEEN '00' AND '23' AND "
    "substr(finalizada_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(finalizada_em,18,2) BETWEEN '00' AND '59')) AND (atualizado_em IS "
    "NULL OR (typeof(atualizado_em)='text' AND length(atualizado_em)=27 AND "
    "length(CAST(atualizado_em AS BLOB))=27 AND atualizado_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(atualizado_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(atualizado_em,1,10),'+0 days') IS substr(atualizado_em,1,10) "
    "AND substr(atualizado_em,12,2) BETWEEN '00' AND '23' AND "
    "substr(atualizado_em,15,2) BETWEEN '00' AND '59' AND "
    "substr(atualizado_em,18,2) BETWEEN '00' AND '59')) AND "
    "atualizado_em>=reservada_em AND (invocacao_em IS NULL OR "
    "(invocacao_em>=reservada_em AND invocacao_em<=atualizado_em)) AND "
    "(finalizada_em IS NULL OR "
    "(finalizada_em>=COALESCE(invocacao_em,reservada_em) AND "
    "finalizada_em=atualizado_em))), \n"
    "\tCONSTRAINT fk_tentativas_envio_alterado_por_usuario_id_usuarios "
    "FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios (id) ON DELETE "
    "RESTRICT\n"
    ")",
    "campanha_execucao_transicoes": "CREATE TABLE campanha_execucao_transicoes (\n"
    "\tid TEXT NOT NULL, \n"
    "\texecucao_id TEXT NOT NULL, \n"
    "\titem_id TEXT, \n"
    "\ttentativa_id TEXT, \n"
    "\tentidade TEXT NOT NULL, \n"
    "\torigem TEXT, \n"
    "\tdestino TEXT NOT NULL, \n"
    "\tversao INTEGER NOT NULL, \n"
    "\tocorrido_em TEXT NOT NULL, \n"
    "\tusuario_id TEXT, \n"
    "\tCONSTRAINT pk_campanha_execucao_transicoes PRIMARY KEY "
    "(id), \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_transicoes_item_id_campanha_execucao_itens "
    "FOREIGN KEY(item_id, execucao_id) REFERENCES "
    "campanha_execucao_itens (id, execucao_id) ON DELETE "
    "RESTRICT, \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_transicoes_tentativa_id_tentativas_envio "
    "FOREIGN KEY(tentativa_id, item_id, execucao_id) REFERENCES "
    "tentativas_envio (id, item_id, execucao_id) ON DELETE "
    "RESTRICT, \n"
    "\tCONSTRAINT ck_campanha_execucao_transicoes_identidade "
    "CHECK (length(id)=36 AND typeof(versao)='integer' AND "
    "versao>=1 AND ((versao=1 AND origem IS NULL) OR (versao>1 "
    "AND origem IS NOT NULL))), \n"
    "\tCONSTRAINT ck_campanha_execucao_transicoes_entidade CHECK "
    "((entidade='execucao' AND item_id IS NULL AND tentativa_id "
    "IS NULL) OR (entidade='item' AND item_id IS NOT NULL AND "
    "tentativa_id IS NULL) OR (entidade='tentativa' AND item_id "
    "IS NOT NULL AND tentativa_id IS NOT NULL)), \n"
    "\tCONSTRAINT ck_campanha_execucao_transicoes_datas CHECK "
    "((ocorrido_em IS NULL OR (typeof(ocorrido_em)='text' AND "
    "length(ocorrido_em)=27 AND length(CAST(ocorrido_em AS "
    "BLOB))=27 AND ocorrido_em GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND substr(ocorrido_em,1,4) BETWEEN '0001' AND '9999' AND "
    "date(substr(ocorrido_em,1,10),'+0 days') IS "
    "substr(ocorrido_em,1,10) AND substr(ocorrido_em,12,2) "
    "BETWEEN '00' AND '23' AND substr(ocorrido_em,15,2) BETWEEN "
    "'00' AND '59' AND substr(ocorrido_em,18,2) BETWEEN '00' AND "
    "'59'))), \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_transicoes_execucao_id_campanha_execucoes "
    "FOREIGN KEY(execucao_id) REFERENCES campanha_execucoes (id) "
    "ON DELETE RESTRICT, \n"
    "\tCONSTRAINT "
    "fk_campanha_execucao_transicoes_usuario_id_usuarios FOREIGN "
    "KEY(usuario_id) REFERENCES usuarios (id) ON DELETE RESTRICT\n"
    ")",
}

INDICES_OPERACIONAIS = {
    "ix_transicoes_entidade_versao": (
        "campanha_execucao_transicoes",
        "CREATE INDEX ix_transicoes_entidade_versao ON campanha_execucao_transicoes "
        "(execucao_id, entidade, item_id, tentativa_id, versao)",
    ),
    "ix_execucoes_aberta": (
        "campanha_execucoes",
        "CREATE UNIQUE INDEX ix_execucoes_aberta ON campanha_execucoes "
        "(campanha_id) WHERE estado IN ('executando','pausando','pausada')",
    ),
    "ix_execucoes_despachante": (
        "campanha_execucoes",
        "CREATE UNIQUE INDEX ix_execucoes_despachante ON "
        "campanha_execucoes (slot) WHERE estado IN "
        "('executando','pausando')",
    ),
    "ix_execucoes_historico": (
        "campanha_execucoes",
        "CREATE INDEX ix_execucoes_historico ON campanha_execucoes (campanha_id, criado_em, id)",
    ),
    "ix_execucao_itens_fila": (
        "campanha_execucao_itens",
        "CREATE INDEX ix_execucao_itens_fila ON campanha_execucao_itens "
        "(execucao_id, estado, ordem)",
    ),
    "ix_tentativa_ativa_global": (
        "tentativas_envio",
        "CREATE UNIQUE INDEX ix_tentativa_ativa_global ON "
        "tentativas_envio (slot) WHERE estado IN "
        "('reservada','iniciada')",
    ),
    "ix_tentativa_ativa_item": (
        "tentativas_envio",
        "CREATE UNIQUE INDEX ix_tentativa_ativa_item ON tentativas_envio "
        "(item_id) WHERE estado IN ('reservada','iniciada')",
    ),
    "ix_tentativas_historico": (
        "tentativas_envio",
        "CREATE INDEX ix_tentativas_historico ON tentativas_envio (execucao_id, item_id, numero)",
    ),
    "ix_transicoes_execucao_versao": (
        "campanha_execucao_transicoes",
        "CREATE UNIQUE INDEX ix_transicoes_execucao_versao ON "
        "campanha_execucao_transicoes (execucao_id, versao) WHERE "
        "entidade='execucao'",
    ),
    "ix_transicoes_historico": (
        "campanha_execucao_transicoes",
        "CREATE INDEX ix_transicoes_historico ON "
        "campanha_execucao_transicoes (execucao_id, ocorrido_em, id)",
    ),
    "ix_transicoes_item_versao": (
        "campanha_execucao_transicoes",
        "CREATE UNIQUE INDEX ix_transicoes_item_versao ON "
        "campanha_execucao_transicoes (item_id, versao) WHERE "
        "entidade='item'",
    ),
    "ix_transicoes_tentativa_versao": (
        "campanha_execucao_transicoes",
        "CREATE UNIQUE INDEX ix_transicoes_tentativa_versao ON "
        "campanha_execucao_transicoes (tentativa_id, versao) WHERE "
        "entidade='tentativa'",
    ),
}


UUID_SQL = (
    "lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||"
    "substr(lower(hex(randomblob(2))),2)||'-8'||substr(lower(hex(randomblob(2))),2)"
    "||'-'||lower(hex(randomblob(6)))"
)
TRIGGERS_OPERACIONAIS = {}


def _trigger(nome, tabela, momento, condicao, corpo):
    quando = f" WHEN {condicao}" if condicao else ""
    TRIGGERS_OPERACIONAIS[nome] = (
        tabela,
        f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} BEGIN {corpo} END",
    )


def _recusar(nome, tabela, momento, condicao):
    _trigger(
        nome,
        tabela,
        momento,
        condicao,
        "SELECT RAISE(ABORT,'Historico operacional ou transicao invalida');",
    )


def _apta(e):
    # SQL garante vinculos estruturais mock. AEAD, sessao e elegibilidade cabem a B1/B2.
    return f"""EXISTS(SELECT 1 FROM campanha_autorizacoes_simulacao a
        JOIN campanha_preparacoes p ON p.id=a.preparacao_id
        JOIN campanhas c ON c.id=p.campanha_id
        JOIN contas_envio conta ON conta.id=c.conta_envio_id
        WHERE a.id={e}.autorizacao_id AND a.preparacao_id={e}.preparacao_id
        AND a.campanha_id={e}.campanha_id AND a.base_id=p.base_id
        AND a.impressao=p.impressao AND a.estado='vigente'
        AND c.status='preparada' AND p.confirmado_em IS NOT NULL AND p.invalidado_em IS NULL
        AND conta.provedor='mock' AND conta.plataforma='whatsapp'
        AND conta.ativo=1 AND conta.removido_em IS NULL
        AND json_extract(p.fotografia_json,'$.configuracao_simulacao.modo')='simulacao'
        AND json_extract(p.fotografia_json,'$.configuracao_simulacao.provedor')='mock'
        AND json_extract(p.fotografia_json,'$.configuracao_simulacao.plataforma')='whatsapp'
        AND json_extract(p.fotografia_json,'$.configuracao_simulacao.conta_envio_id')=conta.id
        AND json_extract(p.fotografia_json,'$.configuracao_simulacao.resumo_configuracao')=a.resumo_configuracao)"""


def _fixos(*nomes):
    return " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in nomes)


def _diario(tabela, entidade, momento):
    execucao = "NEW.id" if entidade == "execucao" else "NEW.execucao_id"
    item = "NULL" if entidade == "execucao" else ("NEW.id" if entidade == "item" else "NEW.item_id")
    tentativa = "NEW.id" if entidade == "tentativa" else "NULL"
    origem = "NULL" if momento == "INSERT" else "OLD.estado"
    return f"""INSERT INTO campanha_execucao_transicoes
        (id,execucao_id,item_id,tentativa_id,entidade,origem,destino,versao,ocorrido_em,usuario_id)
        VALUES ({UUID_SQL},{execucao},{item},{tentativa},'{entidade}',{origem},NEW.estado,
        NEW.versao,NEW.atualizado_em,NEW.alterado_por_usuario_id);"""


def _definir_triggers():
    e, d, i, t, h = TABELAS_OPERACIONAIS
    # Nunca depender do DELETE implicito de REPLACE ou de recursive_triggers.
    conflitos = {
        e: "id=NEW.id OR preparacao_id=NEW.preparacao_id OR autorizacao_id=NEW.autorizacao_id OR (campanha_id=NEW.campanha_id AND estado IN ('executando','pausando','pausada') AND NEW.estado IN ('executando','pausando','pausada')) OR (estado IN ('executando','pausando') AND NEW.estado IN ('executando','pausando'))",
        d: "id=NEW.id OR (campanha_id=NEW.campanha_id AND identidade_contato_id=NEW.identidade_contato_id)",
        i: "id=NEW.id OR (execucao_id=NEW.execucao_id AND (destinatario_id=NEW.destinatario_id OR ordem=NEW.ordem))",
        t: "id=NEW.id OR (item_id=NEW.item_id AND numero=NEW.numero) OR (estado IN ('reservada','iniciada') AND NEW.estado IN ('reservada','iniciada'))",
        h: "id=NEW.id OR (entidade=NEW.entidade AND versao=NEW.versao AND ((entidade='execucao' AND execucao_id=NEW.execucao_id) OR (entidade='item' AND item_id=NEW.item_id) OR (entidade='tentativa' AND tentativa_id=NEW.tentativa_id)))",
    }
    for tabela in (e, d, i, t, h):
        _recusar(tabela + "_sem_delete", tabela, "BEFORE DELETE", None)
        _recusar(
            tabela + "_sem_replace",
            tabela,
            "BEFORE INSERT",
            f"EXISTS(SELECT 1 FROM {tabela} WHERE {conflitos[tabela]})",
        )
    _recusar(d + "_imutavel", d, "BEFORE UPDATE", None)
    _recusar(h + "_imutavel", h, "BEFORE UPDATE", None)
    _recusar(
        e + "_origem",
        e,
        "BEFORE INSERT",
        f"""NEW.estado!='executando' OR NEW.versao!=1 OR NEW.geracao!=1
        OR NEW.criado_em IS NOT NEW.atualizado_em OR NEW.criado_por_usuario_id IS NOT NEW.alterado_por_usuario_id
        OR NOT {_apta("NEW")}
        OR NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanha_autorizacoes_simulacao a ON a.id=NEW.autorizacao_id
        WHERE p.id=NEW.preparacao_id AND NEW.criado_em>=a.autorizado_em
        AND p.incluidos=(SELECT count(*) FROM campanha_preparacao_itens pi WHERE pi.preparacao_id=p.id AND pi.incluido=1))
        OR EXISTS(SELECT 1 FROM tentativas_envio x JOIN campanha_execucoes ex ON ex.id=x.execucao_id WHERE ex.campanha_id=NEW.campanha_id)""",
    )
    _recusar(
        d + "_origem",
        d,
        "BEFORE INSERT",
        """NOT EXISTS(SELECT 1 FROM campanha_execucoes e
        JOIN campanha_preparacao_itens pi ON pi.preparacao_id=e.preparacao_id
        WHERE e.campanha_id=NEW.campanha_id AND pi.identidade_contato_id=NEW.identidade_contato_id AND pi.incluido=1
        AND NOT EXISTS(SELECT 1 FROM campanha_execucao_transicoes h WHERE h.execucao_id=e.id AND h.entidade='execucao'))""",
    )
    _recusar(
        i + "_origem",
        i,
        "BEFORE INSERT",
        """NEW.estado!='pendente' OR NEW.versao!=1 OR NOT EXISTS(
        SELECT 1 FROM campanha_execucoes e JOIN campanha_preparacao_itens pi ON pi.preparacao_id=e.preparacao_id
        WHERE e.id=NEW.execucao_id AND e.campanha_id=NEW.campanha_id AND e.preparacao_id=NEW.preparacao_id
        AND NEW.atualizado_em=e.criado_em AND NEW.alterado_por_usuario_id IS e.criado_por_usuario_id
        AND pi.identidade_contato_id=NEW.identidade_contato_id AND pi.incluido=1
        AND NOT EXISTS(SELECT 1 FROM campanha_execucao_transicoes h WHERE h.execucao_id=e.id AND h.entidade='execucao'))""",
    )
    _trigger(
        e + "_criar_fila",
        e,
        "AFTER INSERT",
        None,
        f"""
        INSERT INTO campanha_destinatarios(id,campanha_id,identidade_contato_id)
        SELECT {UUID_SQL},NEW.campanha_id,pi.identidade_contato_id FROM campanha_preparacao_itens pi
        WHERE pi.preparacao_id=NEW.preparacao_id AND pi.incluido=1 AND NOT EXISTS(
        SELECT 1 FROM campanha_destinatarios d WHERE d.campanha_id=NEW.campanha_id AND d.identidade_contato_id=pi.identidade_contato_id);
        INSERT INTO campanha_execucao_itens(id,execucao_id,campanha_id,preparacao_id,destinatario_id,identidade_contato_id,
        ordem,estado,versao,motivo_codigo,alterado_por_usuario_id,atualizado_em)
        SELECT {UUID_SQL},NEW.id,NEW.campanha_id,NEW.preparacao_id,d.id,pi.identidade_contato_id,
        row_number() OVER (ORDER BY pi.identidade_contato_id),'pendente',1,NULL,NEW.criado_por_usuario_id,NEW.criado_em
        FROM campanha_preparacao_itens pi JOIN campanha_destinatarios d ON d.campanha_id=NEW.campanha_id
        AND d.identidade_contato_id=pi.identidade_contato_id WHERE pi.preparacao_id=NEW.preparacao_id AND pi.incluido=1;
        {_diario(e, "execucao", "INSERT")}""",
    )
    for tabela, entidade in ((i, "item"), (e, "execucao")):
        if entidade == "item":
            _trigger(
                tabela + "_diario_insert",
                tabela,
                "AFTER INSERT",
                None,
                _diario(tabela, entidade, "INSERT"),
            )
        _trigger(
            tabela + "_diario_update",
            tabela,
            "AFTER UPDATE",
            None,
            _diario(tabela, entidade, "UPDATE"),
        )
    # Estado da execucao: sem transicoes terminais e sem pausa/conclusao com tentativa ativa.
    fixos = _fixos(
        "id",
        "campanha_id",
        "preparacao_id",
        "autorizacao_id",
        "criado_em",
        "criado_por_usuario_id",
        "slot",
    )
    _recusar(
        e + "_transicao",
        e,
        "BEFORE UPDATE",
        f"""{fixos} OR NEW.versao!=OLD.versao+1
        OR NEW.atualizado_em<OLD.atualizado_em
        OR NEW.atualizado_em<(SELECT max(atualizado_em) FROM campanha_execucao_itens WHERE execucao_id=OLD.id) OR NOT (
        (NEW.proprietario_id=OLD.proprietario_id AND NEW.geracao=OLD.geracao AND NOT(OLD.estado='pausada' AND NEW.estado='executando')) OR
        (NEW.proprietario_id!=OLD.proprietario_id AND NEW.geracao=OLD.geracao+1 AND
        ((OLD.estado='executando' AND NEW.estado='pausando') OR (OLD.estado='pausada' AND NEW.estado='executando'))))
        OR NOT ((OLD.estado='executando' AND NEW.estado='pausando') OR
        (OLD.estado IN ('executando','pausando') AND NEW.estado='pausada') OR
        (OLD.estado='pausada' AND NEW.estado='executando') OR
        (OLD.estado IN ('executando','pausando','pausada') AND NEW.estado IN ('concluida','concluida_com_falhas','interrompida')))
        OR (NEW.estado='executando' AND NOT {_apta("NEW")})
        OR (NEW.estado='pausada' AND EXISTS(SELECT 1 FROM campanha_execucao_itens WHERE execucao_id=OLD.id AND estado IN ('reservado','processando')))
        OR (NEW.estado IN ('concluida','concluida_com_falhas','interrompida') AND EXISTS(
        SELECT 1 FROM campanha_execucao_itens WHERE execucao_id=OLD.id AND estado IN ('pendente','reservado','processando')))
        OR (NEW.estado='concluida' AND EXISTS(SELECT 1 FROM campanha_execucao_itens WHERE execucao_id=OLD.id AND estado IN ('falhou','desconhecida','interrompido')))
        OR (NEW.estado='concluida_com_falhas' AND NOT EXISTS(SELECT 1 FROM campanha_execucao_itens WHERE execucao_id=OLD.id AND estado IN ('falhou','desconhecida','interrompido')))""",
    )
    fixos = _fixos(
        "id",
        "execucao_id",
        "campanha_id",
        "preparacao_id",
        "destinatario_id",
        "identidade_contato_id",
        "ordem",
    )
    _recusar(
        i + "_transicao",
        i,
        "BEFORE UPDATE",
        f"""{fixos} OR NEW.versao!=OLD.versao+1 OR NEW.atualizado_em<OLD.atualizado_em
        OR NOT EXISTS(SELECT 1 FROM campanha_execucoes e WHERE e.id=OLD.execucao_id AND e.estado IN ('executando','pausando','pausada'))
        OR NOT ((OLD.estado='pendente' AND NEW.estado IN ('ignorado','interrompido') AND NOT EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id AND estado IN ('reservada','iniciada'))) OR
        (OLD.estado='pendente' AND NEW.estado='reservado' AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id AND estado='reservada')) OR
        (OLD.estado='reservado' AND NEW.estado='processando' AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id AND estado='iniciada')) OR
        (OLD.estado='reservado' AND NEW.estado='pendente' AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id AND estado='interrompida' AND numero=(SELECT max(numero) FROM tentativas_envio WHERE item_id=OLD.id))) OR
        (OLD.estado='processando' AND NEW.estado IN ('aceita_provedor','falhou','desconhecida') AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id AND estado=NEW.estado AND numero=(SELECT max(numero) FROM tentativas_envio WHERE item_id=OLD.id))))""",
    )
    _recusar(
        t + "_origem",
        t,
        "BEFORE INSERT",
        f"""NEW.estado!='reservada' OR NEW.versao!=1 OR NEW.atualizado_em!=NEW.reservada_em
        OR NEW.numero!=1+COALESCE((SELECT max(numero) FROM tentativas_envio WHERE item_id=NEW.item_id),0)
        OR EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=NEW.item_id AND estado!='interrompida')
        OR NOT EXISTS(SELECT 1 FROM campanha_execucoes e JOIN campanha_execucao_itens i ON i.execucao_id=e.id
        WHERE e.id=NEW.execucao_id AND i.id=NEW.item_id AND i.estado='pendente' AND e.estado='executando'
        AND e.proprietario_id=NEW.proprietario_id AND e.geracao=NEW.geracao AND NEW.reservada_em>=i.atualizado_em AND {_apta("e")})""",
    )
    fixos = _fixos(
        "id",
        "execucao_id",
        "item_id",
        "numero",
        "proprietario_id",
        "geracao",
        "reservada_em",
        "slot",
    )
    _recusar(
        t + "_transicao",
        t,
        "BEFORE UPDATE",
        f"""{fixos} OR NEW.versao!=OLD.versao+1 OR NEW.atualizado_em<OLD.atualizado_em
        OR (OLD.invocacao_em IS NOT NULL AND NEW.invocacao_em IS NOT OLD.invocacao_em)
        OR NOT ((OLD.estado='reservada' AND NEW.estado IN ('iniciada','interrompida')) OR
        (OLD.estado='iniciada' AND NEW.estado IN ('aceita_provedor','falhou','desconhecida')))
        OR NOT EXISTS(SELECT 1 FROM campanha_execucoes e JOIN campanha_execucao_itens i ON i.execucao_id=e.id
        WHERE e.id=OLD.execucao_id AND i.id=OLD.item_id AND e.estado IN ('executando','pausando')
        AND i.estado=CASE WHEN OLD.estado='reservada' THEN 'reservado' ELSE 'processando' END
        AND ((e.proprietario_id=OLD.proprietario_id AND e.geracao=OLD.geracao) OR
        (e.estado='pausando' AND e.geracao>OLD.geracao AND NEW.estado IN ('interrompida','desconhecida')))
        AND (NEW.estado!='iniciada' OR (e.estado='executando' AND NEW.invocacao_em=NEW.atualizado_em AND {_apta("e")})))""",
    )
    for momento in ("INSERT", "UPDATE"):
        _trigger(
            t + "_item_" + momento.lower(),
            t,
            "AFTER " + momento,
            None,
            f"""
            UPDATE campanha_execucao_itens SET estado=CASE NEW.estado WHEN 'reservada' THEN 'reservado'
            WHEN 'iniciada' THEN 'processando' WHEN 'interrompida' THEN 'pendente' ELSE NEW.estado END,
            versao=versao+1,atualizado_em=NEW.atualizado_em,alterado_por_usuario_id=NEW.alterado_por_usuario_id
            WHERE id=NEW.item_id;
            {_diario(t, "tentativa", momento)}""",
        )
    existentes = " OR ".join(
        f"(NEW.entidade='{entidade}' AND EXISTS(SELECT 1 FROM {tabela} x WHERE x.id={ident} AND x.estado=NEW.destino AND x.versao=NEW.versao AND x.atualizado_em=NEW.ocorrido_em AND x.alterado_por_usuario_id IS NEW.usuario_id))"
        for tabela, entidade, ident in (
            (e, "execucao", "NEW.execucao_id"),
            (i, "item", "NEW.item_id"),
            (t, "tentativa", "NEW.tentativa_id"),
        )
    )
    _recusar(
        h + "_origem",
        h,
        "BEFORE INSERT",
        f"""NOT ({existentes}) OR (NEW.versao>1 AND NOT EXISTS(
        SELECT 1 FROM campanha_execucao_transicoes h WHERE h.execucao_id=NEW.execucao_id AND h.item_id IS NEW.item_id
        AND h.tentativa_id IS NEW.tentativa_id AND h.entidade=NEW.entidade AND h.versao=NEW.versao-1 AND h.destino=NEW.origem))""",
    )
    # B1 deve encerrar antes de revogar/invalidar. Uma tentativa, mesmo sem invocacao, preserva a campanha.
    impedimento = """EXISTS(SELECT 1 FROM campanha_execucoes e WHERE e.campanha_id={campanha}
        AND (e.estado IN ('executando','pausando','pausada') OR EXISTS(SELECT 1 FROM tentativas_envio t WHERE t.execucao_id=e.id)))"""
    _recusar(
        "preparacao_operacional_preservada_0010",
        "campanha_preparacoes",
        "BEFORE UPDATE OF invalidado_em",
        "NEW.invalidado_em IS NOT NULL AND " + impedimento.format(campanha="OLD.campanha_id"),
    )
    _recusar(
        "campanha_operacional_preservada_0010",
        "campanhas",
        "BEFORE UPDATE OF status",
        "NEW.status='rascunho' AND " + impedimento.format(campanha="OLD.id"),
    )


_definir_triggers()


def _objetos_operacionais():
    resultado = {("table", nome, nome): sql for nome, sql in TABELAS_OPERACIONAIS.items()}
    resultado.update(
        {("index", nome, tabela): sql for nome, (tabela, sql) in INDICES_OPERACIONAIS.items()}
    )
    resultado.update(
        {("trigger", nome, tabela): sql for nome, (tabela, sql) in TRIGGERS_OPERACIONAIS.items()}
    )
    for tabela, quantidade in zip(TABELAS_OPERACIONAIS, (4, 3, 4, 3, 1, 1), strict=True):
        for numero in range(1, quantidade + 1):
            resultado["index", f"sqlite_autoindex_{tabela}_{numero}", tabela] = None
    return resultado


# Correcao High: intencao anterior ao evento; nunca remover o historico resolvido.
TABELAS_OPERACIONAIS["consentimento_suspensoes"] = f"""CREATE TABLE consentimento_suspensoes (
    id TEXT NOT NULL,
    escopo_id TEXT NOT NULL,
    contato_id TEXT NOT NULL,
    identidade_contato_id TEXT,
    categoria TEXT,
    tipo TEXT NOT NULL,
    usuario_id TEXT NOT NULL,
    requisicao_hash TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    confirmado_em TEXT,
    evento_id TEXT,
    CONSTRAINT pk_consentimento_suspensoes PRIMARY KEY (id),
    CONSTRAINT fk_consentimento_suspensoes_identidade_contato_id_identidades_contato FOREIGN KEY(identidade_contato_id, contato_id) REFERENCES identidades_contato (id, contato_id) ON DELETE RESTRICT,
    CONSTRAINT ck_consentimento_suspensoes_identidade CHECK (length(id)=36 AND length(requisicao_hash)=64),
    CONSTRAINT ck_consentimento_suspensoes_alvo CHECK ((tipo='bloqueio_global' AND identidade_contato_id IS NULL AND categoria IS NULL) OR (tipo='bloqueio_canal' AND identidade_contato_id IS NOT NULL AND categoria IS NULL) OR (tipo='revogou' AND identidade_contato_id IS NOT NULL AND categoria IS NOT NULL AND categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos'))),
    CONSTRAINT ck_consentimento_suspensoes_confirmacao CHECK ((confirmado_em IS NULL AND evento_id IS NULL) OR (confirmado_em IS NOT NULL AND evento_id IS NOT NULL AND confirmado_em>=criado_em)),
    CONSTRAINT ck_consentimento_suspensoes_datas CHECK ((criado_em IS NULL OR {_data_utc("criado_em")}) AND (confirmado_em IS NULL OR {_data_utc("confirmado_em")})),
    CONSTRAINT fk_consentimento_suspensoes_escopo_id_escopos_comunicacao FOREIGN KEY(escopo_id) REFERENCES escopos_comunicacao (id) ON DELETE RESTRICT,
    CONSTRAINT fk_consentimento_suspensoes_contato_id_contatos FOREIGN KEY(contato_id) REFERENCES contatos (id) ON DELETE RESTRICT,
    CONSTRAINT fk_consentimento_suspensoes_usuario_id_usuarios FOREIGN KEY(usuario_id) REFERENCES usuarios (id) ON DELETE RESTRICT,
    CONSTRAINT fk_consentimento_suspensoes_evento_id_eventos_consentimento FOREIGN KEY(evento_id) REFERENCES eventos_consentimento (id) ON DELETE RESTRICT
)"""
INDICES_OPERACIONAIS["ix_suspensoes_pendentes"] = (
    "consentimento_suspensoes",
    "CREATE INDEX ix_suspensoes_pendentes ON consentimento_suspensoes (contato_id, escopo_id) WHERE confirmado_em IS NULL",
)
_recusar("consentimento_suspensoes_delete", "consentimento_suspensoes", "BEFORE DELETE", "1")
_recusar(
    "consentimento_suspensoes_insert",
    "consentimento_suspensoes",
    "BEFORE INSERT",
    "NEW.confirmado_em IS NOT NULL OR NEW.evento_id IS NOT NULL OR EXISTS(SELECT 1 FROM consentimento_suspensoes WHERE id=NEW.id)",
)
_recusar(
    "consentimento_suspensoes_confirmacao",
    "consentimento_suspensoes",
    "BEFORE UPDATE",
    _fixos(
        "id",
        "escopo_id",
        "contato_id",
        "identidade_contato_id",
        "categoria",
        "tipo",
        "usuario_id",
        "requisicao_hash",
        "criado_em",
    )
    + " OR OLD.confirmado_em IS NOT NULL OR NEW.confirmado_em IS NULL OR NOT EXISTS("
    "SELECT 1 FROM eventos_consentimento v JOIN contatos c ON c.id=v.contato_id "
    "WHERE v.id=NEW.evento_id AND v.contato_id=NEW.contato_id AND v.escopo_id=NEW.escopo_id "
    "AND v.tipo=NEW.tipo AND v.identidade_contato_id IS NEW.identidade_contato_id AND v.categoria IS NEW.categoria "
    "AND v.registrado_em>=NEW.criado_em AND v.registrado_em<=NEW.confirmado_em AND ("
    "(v.tipo='bloqueio_global' AND c.bloqueio_global_em IS NOT NULL) OR "
    "(v.tipo='bloqueio_canal' AND EXISTS(SELECT 1 FROM identidades_contato i WHERE i.id=v.identidade_contato_id AND i.bloqueio_canal_em IS NOT NULL)) OR "
    "(v.tipo='revogou' AND EXISTS(SELECT 1 FROM preferencias_contato p WHERE p.escopo_id=v.escopo_id AND p.identidade_contato_id=v.identidade_contato_id AND p.categoria=v.categoria AND p.estado='bloqueado' AND p.evento_origem_id=v.id))))",
)
