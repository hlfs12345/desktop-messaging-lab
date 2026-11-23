"""Fila comum e executor Telegram sequencial em lote."""

# ruff: noqa: E501

import runpy
from pathlib import Path

from alembic import op

revision = "0018_telegram_executor_lote"
down_revision = "0017_telegram_lote_preparacao"
branch_labels = None
depends_on = None


def _data_utc(coluna):
    return (
        f"typeof({coluna})='text' AND length({coluna})=27 AND "
        f"length(CAST({coluna} AS BLOB))=27 AND "
        f"{coluna} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
        "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]."
        "[0-9][0-9][0-9][0-9][0-9][0-9]Z' AND "
        f"substr({coluna},1,4) BETWEEN '0001' AND '9999' AND "
        f"date(substr({coluna},1,10),'+0 days') IS substr({coluna},1,10) AND "
        f"substr({coluna},12,2) BETWEEN '00' AND '23' AND "
        f"substr({coluna},15,2) BETWEEN '00' AND '59' AND "
        f"substr({coluna},18,2) BETWEEN '00' AND '59'"
    )


def _data_opcional(coluna):
    return f"({coluna} IS NULL OR ({_data_utc(coluna)}))"


def _historica(revisao):
    return runpy.run_path(str(Path(__file__).with_name(revisao + ".py")))

OPERACIONAIS = (
    "campanha_execucao_transicoes",
    "tentativas_envio",
    "campanha_execucao_itens",
    "campanha_destinatarios",
    "campanha_execucoes",
)


def _recusar(nome, tabela, momento, condicao=None, mensagem="Registro imutavel"):
    quando = "" if condicao is None else f" WHEN {condicao}"
    op.execute(
        f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} "
        f"BEGIN SELECT RAISE(ABORT,'{mensagem}'); END"
    )


def _trigger(nome, tabela, momento, condicao, corpo):
    quando = "" if condicao is None else f" WHEN {condicao}"
    op.execute(f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} BEGIN {corpo} END")


def _remover_triggers(tabelas):
    conn = op.get_bind()
    query = "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (" + ",".join(
        "?" for _ in tabelas
    ) + ")"
    nomes = list(conn.exec_driver_sql(query, tuple(tabelas)).scalars())
    for nome in nomes:
        op.execute(f'DROP TRIGGER IF EXISTS "{nome}"')


def _recriar_autorizacao_telegram():
    _remover_triggers(
        (
            "campanha_autorizacoes_envio_telegram",
            "campanha_tentativas_envio_telegram",
        )
    )
    op.execute("DROP TRIGGER IF EXISTS preparacao_com_envio_sem_invalidar")
    op.execute(f"""CREATE TABLE campanha_autorizacoes_envio_telegram_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        operacao_id TEXT NOT NULL UNIQUE CHECK(length(operacao_id)=36),
        requisicao_hash TEXT NOT NULL CHECK(length(requisicao_hash)=64),
        modo TEXT NOT NULL CHECK(modo IN ('unitario','lote')),
        versao INTEGER NOT NULL CHECK(versao>=1),
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT,
        inscricao_id TEXT,
        evento_telegram_id TEXT,
        bot_externo_id TEXT NOT NULL,
        chat_externo_id TEXT,
        conta_versao INTEGER NOT NULL CHECK(conta_versao>=1),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        conteudo_hash TEXT NOT NULL CHECK(length(conteudo_hash)=64),
        categoria TEXT,
        quantidade INTEGER,
        configuracao_hash TEXT,
        intervalo_segundos INTEGER,
        autorizado_por_usuario_id TEXT NOT NULL,
        autorizado_em TEXT NOT NULL,
        CHECK({_data_utc('autorizado_em')}),
        CHECK((modo='unitario' AND identidade_telegram_id IS NOT NULL
          AND inscricao_id IS NOT NULL AND evento_telegram_id IS NOT NULL
          AND chat_externo_id IS NOT NULL AND categoria IS NULL
          AND quantidade IS NULL AND configuracao_hash IS NULL
          AND intervalo_segundos IS NULL) OR
         (modo='lote' AND identidade_telegram_id IS NULL AND inscricao_id IS NULL
          AND evento_telegram_id IS NULL AND chat_externo_id IS NULL
          AND categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos')
          AND quantidade>=1 AND length(configuracao_hash)=64
          AND intervalo_segundos>=5)),
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          REFERENCES campanha_preparacao_itens_telegram
          (preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(evento_telegram_id) REFERENCES eventos_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_autorizacoes_envio_telegram_0018(
        id,operacao_id,requisicao_hash,modo,versao,campanha_id,preparacao_id,
        conta_envio_id,identidade_telegram_id,inscricao_id,evento_telegram_id,
        bot_externo_id,chat_externo_id,conta_versao,impressao,conteudo_hash,
        categoria,quantidade,configuracao_hash,intervalo_segundos,
        autorizado_por_usuario_id,autorizado_em)
        SELECT id,operacao_id,requisicao_hash,'unitario',1,campanha_id,preparacao_id,
        conta_envio_id,identidade_telegram_id,inscricao_id,evento_telegram_id,
        bot_externo_id,chat_externo_id,conta_versao,impressao,conteudo_hash,
        NULL,NULL,NULL,NULL,autorizado_por_usuario_id,autorizado_em
        FROM campanha_autorizacoes_envio_telegram""")
    op.execute(f"""CREATE TABLE campanha_tentativas_envio_telegram_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        autorizacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        chat_externo_id TEXT NOT NULL,
        estado TEXT NOT NULL CHECK(estado IN
          ('iniciada','confirmada','falhou','desconhecida')),
        codigo_resultado TEXT,
        message_id INTEGER,
        retry_after INTEGER,
        iniciada_em TEXT NOT NULL,
        finalizada_em TEXT,
        atualizado_em TEXT NOT NULL,
        CHECK({_data_utc('iniciada_em')} AND {_data_opcional('finalizada_em')}
          AND {_data_utc('atualizado_em')} AND atualizado_em>=iniciada_em
          AND (finalizada_em IS NULL OR finalizada_em=atualizado_em)),
        CHECK((estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL
          AND retry_after IS NULL AND finalizada_em IS NULL) OR
         (estado='confirmada' AND codigo_resultado='aceita' AND message_id>0
          AND retry_after IS NULL AND finalizada_em IS NOT NULL) OR
         (estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL
          AND finalizada_em IS NOT NULL) OR
         (estado='desconhecida' AND codigo_resultado='sem_confirmacao'
          AND message_id IS NULL AND retry_after IS NULL AND finalizada_em IS NOT NULL)),
        CHECK(retry_after IS NULL OR (codigo_resultado='limite_telegram'
          AND retry_after BETWEEN 1 AND 86400)),
        FOREIGN KEY(autorizacao_id)
          REFERENCES campanha_autorizacoes_envio_telegram_0018(id) ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_tentativas_envio_telegram_0018
        SELECT * FROM campanha_tentativas_envio_telegram""")
    op.execute("DROP TABLE campanha_tentativas_envio_telegram")
    op.execute("DROP TABLE campanha_autorizacoes_envio_telegram")
    op.execute(
        "ALTER TABLE campanha_autorizacoes_envio_telegram_0018 "
        "RENAME TO campanha_autorizacoes_envio_telegram"
    )
    op.execute(
        "ALTER TABLE campanha_tentativas_envio_telegram_0018 "
        "RENAME TO campanha_tentativas_envio_telegram"
    )
    op.execute(
        "CREATE INDEX ix_autorizacoes_envio_telegram_campanha ON "
        "campanha_autorizacoes_envio_telegram(campanha_id,autorizado_em)"
    )


def _criar_execucoes_0018():
    op.execute(f"""CREATE TABLE campanha_execucoes_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL UNIQUE,
        autorizacao_id TEXT UNIQUE,
        autorizacao_telegram_id TEXT UNIQUE,
        modo TEXT NOT NULL CHECK((modo='mock' AND autorizacao_id IS NOT NULL
          AND autorizacao_telegram_id IS NULL) OR
          (modo='telegram' AND autorizacao_id IS NULL
          AND autorizacao_telegram_id IS NOT NULL)),
        estado TEXT NOT NULL CHECK(estado IN ('executando','pausando','pausada',
          'concluida','concluida_com_falhas','interrompida')),
        versao INTEGER NOT NULL CHECK(versao>=1),
        proprietario_id TEXT NOT NULL CHECK(length(proprietario_id)=36),
        geracao INTEGER NOT NULL CHECK(geracao>=1),
        slot INTEGER NOT NULL DEFAULT 1 CHECK(slot=1),
        criado_por_usuario_id TEXT NOT NULL,
        alterado_por_usuario_id TEXT,
        criado_em TEXT NOT NULL,
        atualizado_em TEXT NOT NULL CHECK({_data_utc('criado_em')}
          AND {_data_utc('atualizado_em')} AND atualizado_em>=criado_em),
        UNIQUE(id,campanha_id,preparacao_id),
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id) REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizacao_id)
          REFERENCES campanha_autorizacoes_simulacao(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizacao_telegram_id)
          REFERENCES campanha_autorizacoes_envio_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(criado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT,
        FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_execucoes_0018(
        id,campanha_id,preparacao_id,autorizacao_id,autorizacao_telegram_id,modo,
        estado,versao,proprietario_id,geracao,slot,criado_por_usuario_id,
        alterado_por_usuario_id,criado_em,atualizado_em)
        SELECT id,campanha_id,preparacao_id,autorizacao_id,NULL,'mock',estado,versao,
        proprietario_id,geracao,slot,criado_por_usuario_id,alterado_por_usuario_id,
        criado_em,atualizado_em FROM campanha_execucoes""")
    op.execute("""CREATE TABLE campanha_destinatarios_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        campanha_id TEXT NOT NULL,
        identidade_contato_id TEXT,
        identidade_telegram_id TEXT,
        CHECK((identidade_contato_id IS NOT NULL)!=(identidade_telegram_id IS NOT NULL)),
        UNIQUE(campanha_id,identidade_contato_id),
        UNIQUE(campanha_id,identidade_telegram_id),
        UNIQUE(id,campanha_id,identidade_contato_id),
        UNIQUE(id,campanha_id,identidade_telegram_id),
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(identidade_contato_id)
          REFERENCES identidades_contato(id) ON DELETE RESTRICT,
        FOREIGN KEY(identidade_telegram_id)
          REFERENCES identidades_telegram(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_destinatarios_0018
        SELECT id,campanha_id,identidade_contato_id,NULL FROM campanha_destinatarios""")
    op.execute(f"""CREATE TABLE campanha_execucao_itens_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        execucao_id TEXT NOT NULL,
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL,
        destinatario_id TEXT NOT NULL,
        identidade_contato_id TEXT,
        identidade_telegram_id TEXT,
        ordem INTEGER NOT NULL CHECK(ordem>=1),
        estado TEXT NOT NULL CHECK(estado IN ('pendente','reservado','processando',
          'aceita_provedor','falhou','desconhecida','ignorado','interrompido')),
        versao INTEGER NOT NULL CHECK(versao>=1),
        motivo_codigo TEXT,
        alterado_por_usuario_id TEXT,
        atualizado_em TEXT NOT NULL,
        CHECK({_data_utc('atualizado_em')}),
        CHECK((identidade_contato_id IS NOT NULL)!=(identidade_telegram_id IS NOT NULL)),
        CHECK((estado='ignorado' AND motivo_codigo IN
          ('impedimento_atual','consentimento','sincronizacao')) OR
          (estado='interrompido' AND motivo_codigo='encerramento') OR
          (estado NOT IN ('ignorado','interrompido') AND motivo_codigo IS NULL)),
        UNIQUE(id,execucao_id), UNIQUE(execucao_id,destinatario_id),
        UNIQUE(execucao_id,ordem),
        FOREIGN KEY(execucao_id,campanha_id,preparacao_id)
          REFERENCES campanha_execucoes_0018(id,campanha_id,preparacao_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(destinatario_id,campanha_id,identidade_contato_id)
          REFERENCES campanha_destinatarios_0018(id,campanha_id,identidade_contato_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(destinatario_id,campanha_id,identidade_telegram_id)
          REFERENCES campanha_destinatarios_0018(id,campanha_id,identidade_telegram_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,identidade_contato_id)
          REFERENCES campanha_preparacao_itens(preparacao_id,identidade_contato_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,identidade_telegram_id)
          REFERENCES campanha_preparacao_itens_telegram(preparacao_id,identidade_telegram_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_execucao_itens_0018(
        id,execucao_id,campanha_id,preparacao_id,destinatario_id,
        identidade_contato_id,identidade_telegram_id,ordem,estado,versao,
        motivo_codigo,alterado_por_usuario_id,atualizado_em)
        SELECT id,execucao_id,campanha_id,preparacao_id,destinatario_id,
        identidade_contato_id,NULL,ordem,estado,versao,motivo_codigo,
        alterado_por_usuario_id,atualizado_em FROM campanha_execucao_itens""")
    op.execute(f"""CREATE TABLE tentativas_envio_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        execucao_id TEXT NOT NULL, item_id TEXT NOT NULL,
        numero INTEGER NOT NULL CHECK(numero>=1),
        estado TEXT NOT NULL CHECK(estado IN ('reservada','iniciada','interrompida',
          'aceita_provedor','falhou','desconhecida')),
        versao INTEGER NOT NULL CHECK(versao>=1),
        proprietario_id TEXT NOT NULL CHECK(length(proprietario_id)=36),
        geracao INTEGER NOT NULL CHECK(geracao>=1),
        slot INTEGER NOT NULL DEFAULT 1 CHECK(slot=1),
        reservada_em TEXT NOT NULL, invocacao_em TEXT, finalizada_em TEXT,
        atualizado_em TEXT NOT NULL, codigo_resultado TEXT,
        message_id INTEGER, retry_after INTEGER,
        alterado_por_usuario_id TEXT,
        CHECK((estado='reservada' AND invocacao_em IS NULL AND finalizada_em IS NULL
          AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR
         (estado='iniciada' AND invocacao_em IS NOT NULL AND finalizada_em IS NULL
          AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR
         (estado='interrompida' AND invocacao_em IS NULL AND finalizada_em IS NOT NULL
          AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR
         (estado='aceita_provedor' AND invocacao_em IS NOT NULL
          AND finalizada_em IS NOT NULL AND codigo_resultado='aceita'
          AND message_id IS NOT NULL AND retry_after IS NULL) OR
         (estado='falhou' AND invocacao_em IS NOT NULL AND finalizada_em IS NOT NULL
          AND codigo_resultado IS NOT NULL AND message_id IS NULL) OR
         (estado='desconhecida' AND invocacao_em IS NOT NULL
          AND finalizada_em IS NOT NULL AND codigo_resultado='sem_confirmacao'
          AND message_id IS NULL AND retry_after IS NULL)),
        CHECK(message_id IS NULL OR message_id>0),
        CHECK(retry_after IS NULL OR (codigo_resultado='limite_telegram'
          AND retry_after BETWEEN 1 AND 86400)),
        CHECK({_data_utc('reservada_em')} AND {_data_opcional('invocacao_em')}
          AND {_data_opcional('finalizada_em')} AND {_data_utc('atualizado_em')}
          AND atualizado_em>=reservada_em AND
          (invocacao_em IS NULL OR invocacao_em>=reservada_em) AND
          (finalizada_em IS NULL OR (finalizada_em>=COALESCE(invocacao_em,reservada_em)
            AND finalizada_em=atualizado_em))),
        UNIQUE(id,item_id,execucao_id), UNIQUE(item_id,numero),
        FOREIGN KEY(item_id,execucao_id)
          REFERENCES campanha_execucao_itens_0018(id,execucao_id) ON DELETE RESTRICT,
        FOREIGN KEY(alterado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO tentativas_envio_0018(
        id,execucao_id,item_id,numero,estado,versao,proprietario_id,geracao,slot,
        reservada_em,invocacao_em,finalizada_em,atualizado_em,codigo_resultado,
        message_id,retry_after,alterado_por_usuario_id)
        SELECT id,execucao_id,item_id,numero,estado,versao,proprietario_id,geracao,slot,
        reservada_em,invocacao_em,finalizada_em,atualizado_em,codigo_resultado,
        NULL,NULL,alterado_por_usuario_id FROM tentativas_envio""")
    op.execute(f"""CREATE TABLE campanha_execucao_transicoes_0018 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        execucao_id TEXT NOT NULL, item_id TEXT, tentativa_id TEXT,
        entidade TEXT NOT NULL CHECK((entidade='execucao' AND item_id IS NULL
          AND tentativa_id IS NULL) OR (entidade='item' AND item_id IS NOT NULL
          AND tentativa_id IS NULL) OR (entidade='tentativa' AND item_id IS NOT NULL
          AND tentativa_id IS NOT NULL)),
        origem TEXT, destino TEXT NOT NULL, versao INTEGER NOT NULL CHECK(versao>=1),
        ocorrido_em TEXT NOT NULL CHECK({_data_utc('ocorrido_em')}), usuario_id TEXT,
        FOREIGN KEY(execucao_id) REFERENCES campanha_execucoes_0018(id) ON DELETE RESTRICT,
        FOREIGN KEY(item_id,execucao_id)
          REFERENCES campanha_execucao_itens_0018(id,execucao_id) ON DELETE RESTRICT,
        FOREIGN KEY(tentativa_id,item_id,execucao_id)
          REFERENCES tentativas_envio_0018(id,item_id,execucao_id) ON DELETE RESTRICT,
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_execucao_transicoes_0018
        SELECT * FROM campanha_execucao_transicoes""")


def _substituir_execucoes():
    _remover_triggers(OPERACIONAIS)
    op.execute("DROP TRIGGER IF EXISTS preparacao_operacional_preservada_0010")
    op.execute("DROP TRIGGER IF EXISTS campanha_operacional_preservada_0010")
    for tabela in OPERACIONAIS:
        op.execute(f"DROP TABLE {tabela}")
    for tabela in reversed(OPERACIONAIS):
        op.execute(f"ALTER TABLE {tabela}_0018 RENAME TO {tabela}")
    comandos = (
        "CREATE UNIQUE INDEX ix_execucoes_aberta ON campanha_execucoes(campanha_id) WHERE estado IN ('executando','pausando','pausada')",
        "CREATE UNIQUE INDEX ix_execucoes_despachante ON campanha_execucoes(slot) WHERE estado IN ('executando','pausando')",
        "CREATE INDEX ix_execucoes_historico ON campanha_execucoes(campanha_id,criado_em,id)",
        "CREATE INDEX ix_execucao_itens_fila ON campanha_execucao_itens(execucao_id,estado,ordem)",
        "CREATE UNIQUE INDEX ix_tentativa_ativa_item ON tentativas_envio(item_id) WHERE estado IN ('reservada','iniciada')",
        "CREATE UNIQUE INDEX ix_tentativa_ativa_global ON tentativas_envio(slot) WHERE estado IN ('reservada','iniciada')",
        "CREATE INDEX ix_tentativas_historico ON tentativas_envio(execucao_id,item_id,numero)",
        "CREATE UNIQUE INDEX ix_transicoes_execucao_versao ON campanha_execucao_transicoes(execucao_id,versao) WHERE entidade='execucao'",
        "CREATE UNIQUE INDEX ix_transicoes_item_versao ON campanha_execucao_transicoes(item_id,versao) WHERE entidade='item'",
        "CREATE UNIQUE INDEX ix_transicoes_tentativa_versao ON campanha_execucao_transicoes(tentativa_id,versao) WHERE entidade='tentativa'",
        "CREATE INDEX ix_transicoes_historico ON campanha_execucao_transicoes(execucao_id,ocorrido_em,id)",
        "CREATE INDEX ix_transicoes_entidade_versao ON campanha_execucao_transicoes(execucao_id,entidade,item_id,tentativa_id,versao)",
    )
    for comando in comandos:
        op.execute(comando)


def _uuid_sql():
    return "(lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-'||substr('89ab',1+abs(random())%4,1)||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))))"


def _apta(alias):
    return f"""(({alias}.modo='mock' AND EXISTS(SELECT 1
      FROM campanha_autorizacoes_simulacao a
      JOIN campanha_preparacoes p ON p.id=a.preparacao_id
      JOIN campanhas c ON c.id=p.campanha_id
      JOIN contas_envio conta ON conta.id=c.conta_envio_id
      WHERE a.id={alias}.autorizacao_id AND a.preparacao_id={alias}.preparacao_id
      AND a.campanha_id={alias}.campanha_id AND a.estado='vigente'
      AND c.status='preparada' AND p.confirmado_em IS NOT NULL
      AND p.invalidado_em IS NULL AND conta.provedor='mock'
      AND conta.plataforma='whatsapp' AND conta.ativo=1
      AND conta.removido_em IS NULL)) OR
      ({alias}.modo='telegram' AND EXISTS(SELECT 1
      FROM campanha_autorizacoes_envio_telegram a
      JOIN campanha_preparacoes p ON p.id=a.preparacao_id
      JOIN campanhas c ON c.id=p.campanha_id
      JOIN contas_envio conta ON conta.id=c.conta_envio_id
      WHERE a.id={alias}.autorizacao_telegram_id AND a.modo='lote'
      AND a.preparacao_id={alias}.preparacao_id AND a.campanha_id={alias}.campanha_id
      AND a.impressao=p.impressao AND a.quantidade=p.incluidos
      AND a.categoria=c.categoria AND a.conta_envio_id=conta.id
      AND a.conta_versao=conta.versao AND a.bot_externo_id=conta.telegram_bot_id
      AND c.status='preparada' AND p.confirmado_em IS NOT NULL
      AND p.invalidado_em IS NULL AND conta.provedor='telegram_bot_api'
      AND conta.plataforma='telegram' AND conta.ativo=1
      AND conta.removido_em IS NULL)))"""


def _diario(entidade, momento):
    execucao = "NEW.id" if entidade == "execucao" else "NEW.execucao_id"
    item = "NULL" if entidade == "execucao" else ("NEW.id" if entidade == "item" else "NEW.item_id")
    tentativa = "NEW.id" if entidade == "tentativa" else "NULL"
    origem = "NULL" if momento == "INSERT" else "OLD.estado"
    return f"""INSERT INTO campanha_execucao_transicoes
      (id,execucao_id,item_id,tentativa_id,entidade,origem,destino,versao,ocorrido_em,usuario_id)
      VALUES ({_uuid_sql()},{execucao},{item},{tentativa},'{entidade}',{origem},
      NEW.estado,NEW.versao,NEW.atualizado_em,NEW.alterado_por_usuario_id);"""


def _triggers_fila():
    for tabela in OPERACIONAIS:
        _recusar(tabela + "_sem_delete", tabela, "BEFORE DELETE")
    _recusar("campanha_destinatarios_imutavel", "campanha_destinatarios", "BEFORE UPDATE")
    _recusar(
        "campanha_execucao_transicoes_imutavel",
        "campanha_execucao_transicoes",
        "BEFORE UPDATE",
    )
    _recusar(
        "campanha_execucoes_origem",
        "campanha_execucoes",
        "BEFORE INSERT",
        f"NEW.estado!='executando' OR NEW.versao!=1 OR NEW.geracao!=1 OR NOT {_apta('NEW')}",
    )
    _trigger(
        "campanha_execucoes_criar_fila",
        "campanha_execucoes",
        "AFTER INSERT",
        None,
        f"""
        INSERT INTO campanha_destinatarios
          (id,campanha_id,identidade_contato_id,identidade_telegram_id)
        SELECT {_uuid_sql()},NEW.campanha_id,pi.identidade_contato_id,NULL
        FROM campanha_preparacao_itens pi WHERE NEW.modo='mock'
          AND pi.preparacao_id=NEW.preparacao_id AND pi.incluido=1
          AND NOT EXISTS(SELECT 1 FROM campanha_destinatarios d
          WHERE d.campanha_id=NEW.campanha_id
          AND d.identidade_contato_id=pi.identidade_contato_id);
        INSERT INTO campanha_destinatarios
          (id,campanha_id,identidade_contato_id,identidade_telegram_id)
        SELECT {_uuid_sql()},NEW.campanha_id,NULL,pi.identidade_telegram_id
        FROM campanha_preparacao_itens_telegram pi WHERE NEW.modo='telegram'
          AND pi.preparacao_id=NEW.preparacao_id AND pi.incluido=1
          AND NOT EXISTS(SELECT 1 FROM campanha_destinatarios d
          WHERE d.campanha_id=NEW.campanha_id
          AND d.identidade_telegram_id=pi.identidade_telegram_id);
        INSERT INTO campanha_execucao_itens
          (id,execucao_id,campanha_id,preparacao_id,destinatario_id,
          identidade_contato_id,identidade_telegram_id,ordem,estado,versao,
          motivo_codigo,alterado_por_usuario_id,atualizado_em)
        SELECT {_uuid_sql()},NEW.id,NEW.campanha_id,NEW.preparacao_id,d.id,
          pi.identidade_contato_id,NULL,
          row_number() OVER (ORDER BY pi.identidade_contato_id),'pendente',1,NULL,
          NEW.criado_por_usuario_id,NEW.criado_em
        FROM campanha_preparacao_itens pi JOIN campanha_destinatarios d
          ON d.campanha_id=NEW.campanha_id
          AND d.identidade_contato_id=pi.identidade_contato_id
        WHERE NEW.modo='mock' AND pi.preparacao_id=NEW.preparacao_id AND pi.incluido=1;
        INSERT INTO campanha_execucao_itens
          (id,execucao_id,campanha_id,preparacao_id,destinatario_id,
          identidade_contato_id,identidade_telegram_id,ordem,estado,versao,
          motivo_codigo,alterado_por_usuario_id,atualizado_em)
        SELECT {_uuid_sql()},NEW.id,NEW.campanha_id,NEW.preparacao_id,d.id,NULL,
          pi.identidade_telegram_id,
          row_number() OVER (ORDER BY pi.identidade_telegram_id),'pendente',1,NULL,
          NEW.criado_por_usuario_id,NEW.criado_em
        FROM campanha_preparacao_itens_telegram pi JOIN campanha_destinatarios d
          ON d.campanha_id=NEW.campanha_id
          AND d.identidade_telegram_id=pi.identidade_telegram_id
        WHERE NEW.modo='telegram' AND pi.preparacao_id=NEW.preparacao_id
          AND pi.incluido=1;
        {_diario('execucao','INSERT')}""",
    )
    for tabela, entidade in (
        ("campanha_execucao_itens", "item"),
        ("campanha_execucoes", "execucao"),
    ):
        if entidade == "item":
            _trigger(
                tabela + "_diario_insert",
                tabela,
                "AFTER INSERT",
                None,
                _diario(entidade, "INSERT"),
            )
        _trigger(
            tabela + "_diario_update",
            tabela,
            "AFTER UPDATE",
            None,
            _diario(entidade, "UPDATE"),
        )
    _recusar(
        "campanha_execucoes_transicao",
        "campanha_execucoes",
        "BEFORE UPDATE",
        f"""NEW.id IS NOT OLD.id OR NEW.campanha_id IS NOT OLD.campanha_id
        OR NEW.preparacao_id IS NOT OLD.preparacao_id
        OR NEW.autorizacao_id IS NOT OLD.autorizacao_id
        OR NEW.autorizacao_telegram_id IS NOT OLD.autorizacao_telegram_id
        OR NEW.modo IS NOT OLD.modo OR NEW.criado_em IS NOT OLD.criado_em
        OR NEW.criado_por_usuario_id IS NOT OLD.criado_por_usuario_id
        OR NEW.slot IS NOT OLD.slot OR NEW.versao!=OLD.versao+1
        OR NEW.atualizado_em<OLD.atualizado_em OR NOT (
          (NEW.proprietario_id=OLD.proprietario_id AND NEW.geracao=OLD.geracao
           AND NOT(OLD.estado='pausada' AND NEW.estado='executando')) OR
          (NEW.proprietario_id!=OLD.proprietario_id AND NEW.geracao=OLD.geracao+1
           AND ((OLD.estado='executando' AND NEW.estado='pausando') OR
                (OLD.estado='pausada' AND NEW.estado='executando'))))
        OR NOT ((OLD.estado='executando' AND NEW.estado IN ('pausando','pausada')) OR
          (OLD.estado='pausando' AND NEW.estado='pausada') OR
          (OLD.estado='pausada' AND NEW.estado='executando') OR
          (OLD.estado IN ('executando','pausando','pausada') AND
           NEW.estado IN ('concluida','concluida_com_falhas','interrompida')))
        OR (NEW.estado='executando' AND NOT {_apta('NEW')})
        OR (NEW.estado='pausada' AND EXISTS(SELECT 1 FROM campanha_execucao_itens
          WHERE execucao_id=OLD.id AND estado IN ('reservado','processando')))
        OR (NEW.estado IN ('concluida','concluida_com_falhas','interrompida')
          AND EXISTS(SELECT 1 FROM campanha_execucao_itens WHERE execucao_id=OLD.id
          AND estado IN ('pendente','reservado','processando')))
        OR (NEW.estado='concluida' AND EXISTS(SELECT 1 FROM campanha_execucao_itens
          WHERE execucao_id=OLD.id AND estado IN ('falhou','desconhecida','interrompido')))
        OR (NEW.estado='concluida_com_falhas' AND NOT EXISTS(SELECT 1
          FROM campanha_execucao_itens WHERE execucao_id=OLD.id
          AND estado IN ('falhou','desconhecida','interrompido')))""",
    )
    _recusar(
        "campanha_execucao_itens_transicao",
        "campanha_execucao_itens",
        "BEFORE UPDATE",
        """NEW.id IS NOT OLD.id OR NEW.execucao_id IS NOT OLD.execucao_id
        OR NEW.campanha_id IS NOT OLD.campanha_id
        OR NEW.preparacao_id IS NOT OLD.preparacao_id
        OR NEW.destinatario_id IS NOT OLD.destinatario_id
        OR NEW.identidade_contato_id IS NOT OLD.identidade_contato_id
        OR NEW.identidade_telegram_id IS NOT OLD.identidade_telegram_id
        OR NEW.ordem IS NOT OLD.ordem OR NEW.versao!=OLD.versao+1
        OR NEW.atualizado_em<OLD.atualizado_em OR NOT EXISTS(
          SELECT 1 FROM campanha_execucoes e WHERE e.id=OLD.execucao_id
          AND e.estado IN ('executando','pausando','pausada')) OR NOT (
          (OLD.estado='pendente' AND NEW.estado IN ('ignorado','interrompido')
           AND NOT EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id
             AND estado IN ('reservada','iniciada'))) OR
          (OLD.estado='pendente' AND NEW.estado='reservado'
           AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id
             AND estado='reservada')) OR
          (OLD.estado='reservado' AND NEW.estado='processando'
           AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id
             AND estado='iniciada')) OR
          (OLD.estado='reservado' AND NEW.estado='pendente'
           AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id
             AND estado='interrompida')) OR
          (OLD.estado='processando' AND NEW.estado IN
            ('aceita_provedor','falhou','desconhecida')
           AND EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=OLD.id
             AND estado=NEW.estado)))""",
    )
    _recusar(
        "tentativas_envio_origem",
        "tentativas_envio",
        "BEFORE INSERT",
        f"""NEW.estado!='reservada' OR NEW.versao!=1
        OR NEW.atualizado_em!=NEW.reservada_em
        OR NEW.numero!=1+COALESCE((SELECT max(numero) FROM tentativas_envio
          WHERE item_id=NEW.item_id),0)
        OR EXISTS(SELECT 1 FROM tentativas_envio WHERE item_id=NEW.item_id
          AND estado!='interrompida')
        OR NOT EXISTS(SELECT 1 FROM campanha_execucoes e
          JOIN campanha_execucao_itens i ON i.execucao_id=e.id
          WHERE e.id=NEW.execucao_id AND i.id=NEW.item_id AND i.estado='pendente'
          AND e.estado='executando' AND e.proprietario_id=NEW.proprietario_id
          AND e.geracao=NEW.geracao AND {_apta('e')})""",
    )
    _recusar(
        "tentativas_envio_transicao",
        "tentativas_envio",
        "BEFORE UPDATE",
        """NEW.id IS NOT OLD.id OR NEW.execucao_id IS NOT OLD.execucao_id
        OR NEW.item_id IS NOT OLD.item_id OR NEW.numero IS NOT OLD.numero
        OR NEW.proprietario_id IS NOT OLD.proprietario_id
        OR NEW.geracao IS NOT OLD.geracao OR NEW.reservada_em IS NOT OLD.reservada_em
        OR NEW.slot IS NOT OLD.slot OR NEW.versao!=OLD.versao+1
        OR NEW.atualizado_em<OLD.atualizado_em
        OR (OLD.invocacao_em IS NOT NULL AND NEW.invocacao_em IS NOT OLD.invocacao_em)
        OR NOT ((OLD.estado='reservada' AND NEW.estado IN ('iniciada','interrompida')) OR
          (OLD.estado='iniciada' AND NEW.estado IN
            ('aceita_provedor','falhou','desconhecida')))""",
    )
    for momento in ("INSERT", "UPDATE"):
        _trigger(
            "tentativas_envio_item_" + momento.lower(),
            "tentativas_envio",
            "AFTER " + momento,
            None,
            f"""UPDATE campanha_execucao_itens SET estado=CASE NEW.estado
              WHEN 'reservada' THEN 'reservado' WHEN 'iniciada' THEN 'processando'
              WHEN 'interrompida' THEN 'pendente' ELSE NEW.estado END,
              versao=versao+1,atualizado_em=NEW.atualizado_em,
              alterado_por_usuario_id=NEW.alterado_por_usuario_id WHERE id=NEW.item_id;
              {_diario('tentativa',momento)}""",
        )
    _recusar(
        "preparacao_operacional_preservada_0018",
        "campanha_preparacoes",
        "BEFORE UPDATE OF invalidado_em",
        "NEW.invalidado_em IS NOT NULL AND EXISTS(SELECT 1 FROM campanha_execucoes e "
        "WHERE e.campanha_id=OLD.campanha_id AND (e.estado IN "
        "('executando','pausando','pausada') OR EXISTS(SELECT 1 FROM tentativas_envio t "
        "WHERE t.execucao_id=e.id)))",
    )
    _recusar(
        "campanha_operacional_preservada_0018",
        "campanhas",
        "BEFORE UPDATE OF status",
        "NEW.status='rascunho' AND EXISTS(SELECT 1 FROM campanha_execucoes e "
        "WHERE e.campanha_id=OLD.id AND (e.estado IN "
        "('executando','pausando','pausada') OR EXISTS(SELECT 1 FROM tentativas_envio t "
        "WHERE t.execucao_id=e.id)))",
    )
    conflitos = {
        "campanha_execucoes": "id=NEW.id OR preparacao_id=NEW.preparacao_id OR autorizacao_id=NEW.autorizacao_id OR autorizacao_telegram_id=NEW.autorizacao_telegram_id",
        "campanha_destinatarios": "id=NEW.id OR (campanha_id=NEW.campanha_id AND identidade_contato_id=NEW.identidade_contato_id) OR (campanha_id=NEW.campanha_id AND identidade_telegram_id=NEW.identidade_telegram_id)",
        "campanha_execucao_itens": "id=NEW.id OR (execucao_id=NEW.execucao_id AND (destinatario_id=NEW.destinatario_id OR ordem=NEW.ordem))",
        "tentativas_envio": "id=NEW.id OR (item_id=NEW.item_id AND numero=NEW.numero) OR (estado IN ('reservada','iniciada') AND NEW.estado IN ('reservada','iniciada'))",
        "campanha_execucao_transicoes": "id=NEW.id OR (entidade=NEW.entidade AND versao=NEW.versao AND ((entidade='execucao' AND execucao_id=NEW.execucao_id) OR (entidade='item' AND item_id=NEW.item_id) OR (entidade='tentativa' AND tentativa_id=NEW.tentativa_id)))",
    }
    for tabela, condicao in conflitos.items():
        _recusar(
            tabela + "_sem_replace",
            tabela,
            "BEFORE INSERT",
            f"EXISTS(SELECT 1 FROM {tabela} WHERE {condicao})",
        )


def _triggers_telegram():
    _recusar(
        "conta_envio_sincronizacao_telegram_data",
        "contas_envio",
        "BEFORE UPDATE OF telegram_sincronizado_em,telegram_lacuna_desde",
        f"NOT ({_data_opcional('NEW.telegram_sincronizado_em')} AND "
        f"{_data_opcional('NEW.telegram_lacuna_desde')})",
        "Data Telegram invalida",
    )
    _recusar(
        "autorizacao_envio_telegram_valida",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        """NOT EXISTS(SELECT 1 FROM campanha_preparacoes p
        JOIN campanhas c ON c.id=p.campanha_id
        JOIN contas_envio a ON a.id=NEW.conta_envio_id
        WHERE p.id=NEW.preparacao_id AND p.campanha_id=NEW.campanha_id
        AND p.confirmado_em IS NOT NULL AND p.invalidado_em IS NULL
        AND p.impressao=NEW.impressao AND c.status='preparada'
        AND c.conta_envio_id=NEW.conta_envio_id AND a.versao=NEW.conta_versao
        AND a.telegram_bot_id=NEW.bot_externo_id
        AND ((NEW.modo='unitario' AND p.incluidos=1 AND p.excluidos=0
          AND EXISTS(SELECT 1 FROM inscricoes_conta i
            JOIN identidades_telegram t ON t.id=i.identidade_telegram_id
            JOIN eventos_telegram e ON e.id=NEW.evento_telegram_id
            WHERE i.id=NEW.inscricao_id AND t.id=NEW.identidade_telegram_id
            AND t.chat_externo_id=NEW.chat_externo_id
            AND i.conta_envio_id=NEW.conta_envio_id AND i.estado='iniciada'
            AND e.inscricao_id=i.id AND e.conta_envio_id=a.id
            AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria
            AND e.tipo='autorizou' AND e.confirmado=1
            AND NOT EXISTS(SELECT 1 FROM eventos_telegram r
              WHERE r.inscricao_id=e.inscricao_id AND r.tipo='revogou'
              AND r.registrado_em>=e.registrado_em))) OR
          (NEW.modo='lote' AND NEW.quantidade=p.incluidos
           AND NEW.categoria=c.categoria)))""",
        "Autorizacao Telegram incompativel",
    )
    for acao in ("UPDATE", "DELETE"):
        _recusar(
            "autorizacao_envio_telegram_sem_" + acao.lower(),
            "campanha_autorizacoes_envio_telegram",
            "BEFORE " + acao,
        )
    _recusar(
        "autorizacao_envio_telegram_sem_replace",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        "EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_telegram "
        "WHERE preparacao_id=NEW.preparacao_id OR operacao_id=NEW.operacao_id)",
    )
    _recusar(
        "tentativa_envio_telegram_fixa",
        "campanha_tentativas_envio_telegram",
        "BEFORE UPDATE",
        "OLD.estado!='iniciada' OR NEW.id IS NOT OLD.id OR "
        "NEW.autorizacao_id IS NOT OLD.autorizacao_id OR "
        "NEW.conta_envio_id IS NOT OLD.conta_envio_id OR "
        "NEW.chat_externo_id IS NOT OLD.chat_externo_id OR "
        "NEW.iniciada_em IS NOT OLD.iniciada_em",
    )
    _recusar(
        "tentativa_envio_telegram_sem_delete",
        "campanha_tentativas_envio_telegram",
        "BEFORE DELETE",
    )
    _recusar(
        "preparacao_com_envio_sem_invalidar",
        "campanha_preparacoes",
        "BEFORE UPDATE OF invalidado_em",
        "NEW.invalidado_em IS NOT OLD.invalidado_em AND EXISTS(SELECT 1 FROM "
        "campanha_autorizacoes_envio_telegram a WHERE a.preparacao_id=OLD.id)",
    )


def upgrade():
    op.execute("ALTER TABLE contas_envio ADD COLUMN telegram_sincronizado_em TEXT")
    op.execute("ALTER TABLE contas_envio ADD COLUMN telegram_lacuna_desde TEXT")
    _recriar_autorizacao_telegram()
    _criar_execucoes_0018()
    _substituir_execucoes()
    op.execute(f"""CREATE TABLE telegram_controle_envio (
        conta_envio_id TEXT NOT NULL PRIMARY KEY,
        liberar_em TEXT,
        impedimento_codigo TEXT,
        impedido_ate TEXT,
        atualizado_em TEXT NOT NULL,
        CHECK({_data_opcional('liberar_em')} AND {_data_opcional('impedido_ate')}
          AND {_data_utc('atualizado_em')}),
        CHECK((impedimento_codigo IS NULL AND impedido_ate IS NULL) OR
          (impedimento_codigo='limite_telegram' AND impedido_ate IS NOT NULL) OR
          (impedimento_codigo='sincronizacao' AND impedido_ate IS NULL)),
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT)""")
    _triggers_fila()
    _triggers_telegram()


def _restaurar_fila_mock():
    historica = _historica("0010_autorizacoes_utc")
    tabelas = historica["TABELAS_OPERACIONAIS"]
    nomes = set(OPERACIONAIS)
    for tabela, sql in tabelas.items():
        if tabela not in nomes:
            continue
        temporario = sql
        for nome in OPERACIONAIS:
            temporario = temporario.replace(nome, nome + "_0017")
        op.execute(temporario)
    op.execute("""INSERT INTO campanha_execucoes_0017(
        id,campanha_id,preparacao_id,autorizacao_id,estado,versao,
        proprietario_id,geracao,slot,criado_por_usuario_id,
        alterado_por_usuario_id,criado_em,atualizado_em)
        SELECT id,campanha_id,preparacao_id,autorizacao_id,estado,versao,
        proprietario_id,geracao,slot,criado_por_usuario_id,
        alterado_por_usuario_id,criado_em,atualizado_em FROM campanha_execucoes""")
    op.execute("""INSERT INTO campanha_destinatarios_0017
        SELECT id,campanha_id,identidade_contato_id FROM campanha_destinatarios""")
    op.execute("""INSERT INTO campanha_execucao_itens_0017(
        id,execucao_id,campanha_id,preparacao_id,destinatario_id,
        identidade_contato_id,ordem,estado,versao,motivo_codigo,
        alterado_por_usuario_id,atualizado_em)
        SELECT id,execucao_id,campanha_id,preparacao_id,destinatario_id,
        identidade_contato_id,ordem,estado,versao,motivo_codigo,
        alterado_por_usuario_id,atualizado_em FROM campanha_execucao_itens""")
    op.execute("""INSERT INTO tentativas_envio_0017(
        id,execucao_id,item_id,numero,estado,versao,proprietario_id,geracao,
        slot,reservada_em,invocacao_em,finalizada_em,atualizado_em,
        codigo_resultado,alterado_por_usuario_id)
        SELECT id,execucao_id,item_id,numero,estado,versao,proprietario_id,geracao,
        slot,reservada_em,invocacao_em,finalizada_em,atualizado_em,
        codigo_resultado,alterado_por_usuario_id FROM tentativas_envio""")
    op.execute("""INSERT INTO campanha_execucao_transicoes_0017
        SELECT id,execucao_id,item_id,tentativa_id,entidade,origem,destino,
        versao,ocorrido_em,usuario_id FROM campanha_execucao_transicoes""")
    _remover_triggers(OPERACIONAIS)
    op.execute("DROP TRIGGER IF EXISTS preparacao_operacional_preservada_0018")
    op.execute("DROP TRIGGER IF EXISTS campanha_operacional_preservada_0018")
    for tabela in OPERACIONAIS:
        op.execute(f"DROP TABLE {tabela}")
    for tabela in reversed(OPERACIONAIS):
        op.execute(f"ALTER TABLE {tabela}_0017 RENAME TO {tabela}")
    for _, (tabela, sql) in historica["INDICES_OPERACIONAIS"].items():
        if tabela in nomes:
            op.execute(sql)
    for _, (tabela, sql) in historica["TRIGGERS_OPERACIONAIS"].items():
        if tabela in nomes:
            op.execute(sql)


def _restaurar_envio_unitario():
    _remover_triggers(
        ("campanha_autorizacoes_envio_telegram", "campanha_tentativas_envio_telegram")
    )
    op.execute("DROP TRIGGER IF EXISTS preparacao_com_envio_sem_invalidar")
    op.execute("DROP INDEX ix_autorizacoes_envio_telegram_campanha")
    op.execute("""CREATE TABLE campanha_autorizacoes_envio_telegram_0017 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        operacao_id TEXT NOT NULL UNIQUE CHECK(length(operacao_id)=36),
        requisicao_hash TEXT NOT NULL CHECK(length(requisicao_hash)=64),
        campanha_id TEXT NOT NULL, preparacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL, identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL, evento_telegram_id TEXT NOT NULL,
        bot_externo_id TEXT NOT NULL, chat_externo_id TEXT NOT NULL,
        conta_versao INTEGER NOT NULL CHECK(conta_versao>=1),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        conteudo_hash TEXT NOT NULL CHECK(length(conteudo_hash)=64),
        autorizado_por_usuario_id TEXT NOT NULL, autorizado_em TEXT NOT NULL,
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          REFERENCES campanha_preparacao_itens_telegram
          (preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(evento_telegram_id) REFERENCES eventos_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_autorizacoes_envio_telegram_0017
        SELECT id,operacao_id,requisicao_hash,campanha_id,preparacao_id,
        conta_envio_id,identidade_telegram_id,inscricao_id,evento_telegram_id,
        bot_externo_id,chat_externo_id,conta_versao,impressao,conteudo_hash,
        autorizado_por_usuario_id,autorizado_em
        FROM campanha_autorizacoes_envio_telegram WHERE modo='unitario'""")
    op.execute("""CREATE TABLE campanha_tentativas_envio_telegram_0017 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        autorizacao_id TEXT NOT NULL UNIQUE, conta_envio_id TEXT NOT NULL,
        chat_externo_id TEXT NOT NULL,
        estado TEXT NOT NULL CHECK(estado IN
          ('iniciada','confirmada','falhou','desconhecida')),
        codigo_resultado TEXT, message_id INTEGER, retry_after INTEGER,
        iniciada_em TEXT NOT NULL, finalizada_em TEXT, atualizado_em TEXT NOT NULL,
        CHECK((estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL
          AND retry_after IS NULL AND finalizada_em IS NULL) OR
         (estado='confirmada' AND codigo_resultado='aceita' AND message_id>0
          AND retry_after IS NULL AND finalizada_em IS NOT NULL) OR
         (estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL
          AND finalizada_em IS NOT NULL) OR
         (estado='desconhecida' AND codigo_resultado='sem_confirmacao'
          AND message_id IS NULL AND retry_after IS NULL AND finalizada_em IS NOT NULL)),
        CHECK(retry_after IS NULL OR (codigo_resultado='limite_telegram'
          AND retry_after BETWEEN 1 AND 86400)),
        FOREIGN KEY(autorizacao_id)
          REFERENCES campanha_autorizacoes_envio_telegram_0017(id) ON DELETE RESTRICT,
        FOREIGN KEY(conta_envio_id) REFERENCES contas_envio(id) ON DELETE RESTRICT)""")
    op.execute("""INSERT INTO campanha_tentativas_envio_telegram_0017
        SELECT * FROM campanha_tentativas_envio_telegram""")
    op.execute("DROP TABLE campanha_tentativas_envio_telegram")
    op.execute("DROP TABLE campanha_autorizacoes_envio_telegram")
    op.execute("ALTER TABLE campanha_autorizacoes_envio_telegram_0017 RENAME TO campanha_autorizacoes_envio_telegram")
    op.execute("ALTER TABLE campanha_tentativas_envio_telegram_0017 RENAME TO campanha_tentativas_envio_telegram")
    op.execute("CREATE INDEX ix_autorizacoes_envio_telegram_campanha ON campanha_autorizacoes_envio_telegram(campanha_id,autorizado_em)")
    historica = _historica("0017_telegram_lote_preparacao")
    historica["_remover_gatilhos"]()
    historica["_criar_gatilhos"](lote=True)


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("""SELECT
        EXISTS(SELECT 1 FROM campanha_execucoes WHERE modo='telegram') OR
        EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_telegram WHERE modo='lote')""").scalar():
        raise RuntimeError("Downgrade recusado: preserve o executor Telegram e a fila comum.")
    _restaurar_fila_mock()
    _restaurar_envio_unitario()
    op.execute("DROP TRIGGER IF EXISTS conta_envio_sincronizacao_telegram_data")
    op.execute("DROP TABLE telegram_controle_envio")
    op.execute("ALTER TABLE contas_envio DROP COLUMN telegram_lacuna_desde")
    op.execute("ALTER TABLE contas_envio DROP COLUMN telegram_sincronizado_em")
