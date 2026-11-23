"""Selecao e fotografia Telegram com multiplos destinatarios."""

from alembic import op

revision = "0017_telegram_lote_preparacao"
down_revision = "0016_telegram_reativacao"
branch_labels = None
depends_on = None


TRIGGERS = (
    "selecao_telegram_compativel_insert",
    "campanha_selecao_telegram_conta_fixa",
    "campanha_selecoes_telegram_congelada_insert",
    "campanha_selecoes_telegram_congelada_update",
    "campanha_selecoes_telegram_congelada_delete",
    "preparacao_telegram_origem",
    "campanha_preparacao_itens_telegram_sem_update",
    "campanha_preparacao_itens_telegram_sem_delete",
    "campanha_preparacao_itens_telegram_sem_replace",
    "autorizacao_envio_telegram_valida",
    "autorizacao_envio_telegram_sem_update",
    "autorizacao_envio_telegram_sem_delete",
    "autorizacao_envio_telegram_sem_replace",
    "tentativa_envio_telegram_fixa",
    "tentativa_envio_telegram_sem_delete",
    "preparacao_com_envio_sem_invalidar",
)


def _recusar(nome, tabela, momento, condicao=None, mensagem="Registro imutavel"):
    quando = "" if condicao is None else f" WHEN {condicao}"
    op.execute(
        f"CREATE TRIGGER {nome} {momento} ON {tabela}{quando} "
        f"BEGIN SELECT RAISE(ABORT,'{mensagem}'); END"
    )


def _remover_gatilhos():
    for nome in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {nome}")


def _criar_tabelas(*, lote):
    chave_selecao = (
        "PRIMARY KEY(campanha_id,identidade_telegram_id)"
        if lote
        else "PRIMARY KEY(campanha_id)"
    )
    op.execute(f"""CREATE TABLE campanha_selecoes_telegram_0017 (
        campanha_id TEXT NOT NULL CHECK(length(campanha_id)=36),
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        selecionado_em TEXT NOT NULL,
        {chave_selecao},
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(inscricao_id,conta_envio_id)
            REFERENCES inscricoes_conta(id,conta_envio_id) ON DELETE RESTRICT,
        FOREIGN KEY(inscricao_id,identidade_telegram_id)
            REFERENCES inscricoes_conta(id,identidade_telegram_id) ON DELETE RESTRICT)""")
    if lote:
        campos = """incluido INTEGER NOT NULL CHECK(incluido IN (0,1)),
        motivos_json TEXT NOT NULL,
        evento_telegram_id TEXT,
        nonce BLOB,
        conteudo_cifrado BLOB,
        bytes_texto INTEGER NOT NULL,
        PRIMARY KEY(preparacao_id,identidade_telegram_id),
        CHECK((incluido=1 AND evento_telegram_id IS NOT NULL AND nonce IS NOT NULL
            AND conteudo_cifrado IS NOT NULL AND length(nonce)=12
            AND length(conteudo_cifrado)=bytes_texto+16
            AND bytes_texto BETWEEN 1 AND 65536 AND motivos_json='[]') OR
          (incluido=0 AND nonce IS NULL AND conteudo_cifrado IS NULL
            AND bytes_texto=0 AND motivos_json!='[]'))"""
    else:
        campos = """evento_telegram_id TEXT NOT NULL,
        nonce BLOB NOT NULL CHECK(length(nonce)=12),
        conteudo_cifrado BLOB NOT NULL,
        bytes_texto INTEGER NOT NULL CHECK(bytes_texto BETWEEN 1 AND 65536
            AND length(conteudo_cifrado)=bytes_texto+16),
        PRIMARY KEY(preparacao_id)"""
    op.execute(f"""CREATE TABLE campanha_preparacao_itens_telegram_0017 (
        preparacao_id TEXT NOT NULL,
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        {campos},
        FOREIGN KEY(preparacao_id) REFERENCES campanha_preparacoes(id) ON DELETE RESTRICT,
        FOREIGN KEY(identidade_telegram_id)
            REFERENCES identidades_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(inscricao_id,conta_envio_id)
            REFERENCES inscricoes_conta(id,conta_envio_id) ON DELETE RESTRICT,
        FOREIGN KEY(inscricao_id,identidade_telegram_id)
            REFERENCES inscricoes_conta(id,identidade_telegram_id) ON DELETE RESTRICT,
        FOREIGN KEY(evento_telegram_id)
            REFERENCES eventos_telegram(id) ON DELETE RESTRICT)""")
    op.execute(
        "CREATE UNIQUE INDEX uq_preparacao_telegram_vinculo_0017 ON "
        "campanha_preparacao_itens_telegram_0017"
        "(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)"
    )
    op.execute("""CREATE TABLE campanha_autorizacoes_envio_telegram_0017 (
        id TEXT NOT NULL PRIMARY KEY CHECK(length(id)=36),
        operacao_id TEXT NOT NULL UNIQUE CHECK(length(operacao_id)=36),
        requisicao_hash TEXT NOT NULL CHECK(length(requisicao_hash)=64),
        campanha_id TEXT NOT NULL,
        preparacao_id TEXT NOT NULL UNIQUE,
        conta_envio_id TEXT NOT NULL,
        identidade_telegram_id TEXT NOT NULL,
        inscricao_id TEXT NOT NULL,
        evento_telegram_id TEXT NOT NULL,
        bot_externo_id TEXT NOT NULL,
        chat_externo_id TEXT NOT NULL,
        conta_versao INTEGER NOT NULL CHECK(conta_versao>=1),
        impressao TEXT NOT NULL CHECK(length(impressao)=64),
        conteudo_hash TEXT NOT NULL CHECK(length(conteudo_hash)=64),
        autorizado_por_usuario_id TEXT NOT NULL,
        autorizado_em TEXT NOT NULL,
        FOREIGN KEY(campanha_id) REFERENCES campanhas(id) ON DELETE RESTRICT,
        FOREIGN KEY(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          REFERENCES campanha_preparacao_itens_telegram_0017
          (preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)
          ON DELETE RESTRICT,
        FOREIGN KEY(evento_telegram_id) REFERENCES eventos_telegram(id) ON DELETE RESTRICT,
        FOREIGN KEY(autorizado_por_usuario_id) REFERENCES usuarios(id) ON DELETE RESTRICT)""")
    op.execute("""CREATE TABLE campanha_tentativas_envio_telegram_0017 (
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


def _copiar(*, lote):
    op.execute("""INSERT INTO campanha_selecoes_telegram_0017
        SELECT campanha_id,conta_envio_id,identidade_telegram_id,inscricao_id,selecionado_em
        FROM campanha_selecoes_telegram""")
    destino = (
        "preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id,"
        "incluido,motivos_json,evento_telegram_id,nonce,conteudo_cifrado,bytes_texto"
        if lote
        else "preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id,"
        "evento_telegram_id,nonce,conteudo_cifrado,bytes_texto"
    )
    origem = (
        "preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id,"
        "1,'[]',evento_telegram_id,nonce,conteudo_cifrado,bytes_texto"
        if lote
        else "preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id,"
        "evento_telegram_id,nonce,conteudo_cifrado,bytes_texto"
    )
    op.execute(
        f"INSERT INTO campanha_preparacao_itens_telegram_0017({destino}) "
        f"SELECT {origem} FROM campanha_preparacao_itens_telegram"
    )
    colunas = (
        "id,operacao_id,requisicao_hash,campanha_id,preparacao_id,conta_envio_id,"
        "identidade_telegram_id,inscricao_id,evento_telegram_id,bot_externo_id,"
        "chat_externo_id,conta_versao,impressao,conteudo_hash,"
        "autorizado_por_usuario_id,autorizado_em"
    )
    op.execute(
        "INSERT INTO campanha_autorizacoes_envio_telegram_0017 "
        f"SELECT {colunas} FROM campanha_autorizacoes_envio_telegram"
    )
    colunas = (
        "id,autorizacao_id,conta_envio_id,chat_externo_id,estado,codigo_resultado,"
        "message_id,retry_after,iniciada_em,finalizada_em,atualizado_em"
    )
    op.execute(
        "INSERT INTO campanha_tentativas_envio_telegram_0017 "
        f"SELECT {colunas} FROM campanha_tentativas_envio_telegram"
    )


def _substituir():
    for tabela in (
        "campanha_tentativas_envio_telegram",
        "campanha_autorizacoes_envio_telegram",
        "campanha_preparacao_itens_telegram",
        "campanha_selecoes_telegram",
    ):
        op.execute(f"DROP TABLE {tabela}")
    for tabela in (
        "campanha_selecoes_telegram",
        "campanha_preparacao_itens_telegram",
        "campanha_autorizacoes_envio_telegram",
        "campanha_tentativas_envio_telegram",
    ):
        op.execute(f"ALTER TABLE {tabela}_0017 RENAME TO {tabela}")
    op.execute(
        "CREATE INDEX ix_preparacao_telegram_identidade ON "
        "campanha_preparacao_itens_telegram(identidade_telegram_id,preparacao_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_preparacao_telegram_nonce ON "
        "campanha_preparacao_itens_telegram(preparacao_id,nonce)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_preparacao_telegram_vinculo ON "
        "campanha_preparacao_itens_telegram"
        "(preparacao_id,conta_envio_id,identidade_telegram_id,inscricao_id)"
    )
    op.execute("DROP INDEX uq_preparacao_telegram_vinculo_0017")
    op.execute(
        "CREATE INDEX ix_autorizacoes_envio_telegram_campanha ON "
        "campanha_autorizacoes_envio_telegram(campanha_id,autorizado_em)"
    )


def _criar_gatilhos(*, lote):
    _recusar(
        "selecao_telegram_compativel_insert",
        "campanha_selecoes_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanhas c JOIN contas_envio a "
        "ON a.id=c.conta_envio_id JOIN inscricoes_conta i ON i.id=NEW.inscricao_id "
        "WHERE c.id=NEW.campanha_id AND c.status='rascunho' "
        "AND c.conta_envio_id=NEW.conta_envio_id AND a.plataforma='telegram' "
        "AND a.provedor='telegram_bot_api' AND i.conta_envio_id=NEW.conta_envio_id "
        "AND i.identidade_telegram_id=NEW.identidade_telegram_id)",
        "Destino Telegram incompativel",
    )
    _recusar(
        "campanha_selecao_telegram_conta_fixa",
        "campanhas",
        "BEFORE UPDATE OF conta_envio_id",
        "NEW.conta_envio_id IS NOT OLD.conta_envio_id AND EXISTS(SELECT 1 FROM "
        "campanha_selecoes_telegram s WHERE s.campanha_id=OLD.id)",
        "Remova o destino Telegram antes de trocar a conta",
    )
    for acao in ("INSERT", "UPDATE", "DELETE"):
        row = "OLD" if acao == "DELETE" else "NEW"
        _recusar(
            f"campanha_selecoes_telegram_congelada_{acao.lower()}",
            "campanha_selecoes_telegram",
            f"BEFORE {acao}",
            f"EXISTS(SELECT 1 FROM campanhas WHERE id={row}.campanha_id "
            "AND status='preparada')",
            "Selecao congelada",
        )
    origem = (
        "AND ((NEW.incluido=0) OR (i.estado='iniciada' "
        "AND e.id=NEW.evento_telegram_id AND e.inscricao_id=NEW.inscricao_id "
        "AND e.conta_envio_id=NEW.conta_envio_id AND e.escopo_id=c.escopo_id "
        "AND e.categoria=c.categoria AND e.tipo='autorizou' AND e.confirmado=1 "
        "AND NOT EXISTS(SELECT 1 FROM eventos_telegram r "
        "WHERE r.inscricao_id=e.inscricao_id AND r.tipo='revogou' "
        "AND r.registrado_em>=e.registrado_em)))"
        if lote
        else "AND i.estado='iniciada' AND e.id=NEW.evento_telegram_id "
        "AND e.inscricao_id=NEW.inscricao_id AND e.conta_envio_id=NEW.conta_envio_id "
        "AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria "
        "AND e.tipo='autorizou' AND e.confirmado=1"
    )
    _recusar(
        "preparacao_telegram_origem",
        "campanha_preparacao_itens_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c "
        "ON c.id=p.campanha_id JOIN campanha_selecoes_telegram s "
        "ON s.campanha_id=c.id JOIN inscricoes_conta i ON i.id=NEW.inscricao_id "
        "LEFT JOIN eventos_telegram e ON e.id=NEW.evento_telegram_id "
        "WHERE p.id=NEW.preparacao_id AND c.conta_envio_id=NEW.conta_envio_id "
        "AND s.conta_envio_id=NEW.conta_envio_id "
        "AND s.identidade_telegram_id=NEW.identidade_telegram_id "
        f"AND s.inscricao_id=NEW.inscricao_id {origem})",
        "Preparacao Telegram incompativel",
    )
    _recusar(
        "campanha_preparacao_itens_telegram_sem_update",
        "campanha_preparacao_itens_telegram",
        "BEFORE UPDATE",
    )
    _recusar(
        "campanha_preparacao_itens_telegram_sem_delete",
        "campanha_preparacao_itens_telegram",
        "BEFORE DELETE",
    )
    if not lote:
        _recusar(
            "campanha_preparacao_itens_telegram_sem_replace",
            "campanha_preparacao_itens_telegram",
            "BEFORE INSERT",
            "EXISTS(SELECT 1 FROM campanha_preparacao_itens_telegram "
            "WHERE preparacao_id=NEW.preparacao_id)",
            "Registro existente",
        )
    unidade = (
        "AND p.incluidos=1 AND p.excluidos=0 AND EXISTS(SELECT 1 FROM "
        "campanha_preparacao_itens_telegram x WHERE x.preparacao_id=p.id "
        "AND x.incluido=1 GROUP BY x.preparacao_id HAVING COUNT(*)=1) "
        if lote
        else ""
    )
    _recusar(
        "autorizacao_envio_telegram_valida",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        "NOT EXISTS(SELECT 1 FROM campanha_preparacoes p JOIN campanhas c "
        "ON c.id=p.campanha_id JOIN contas_envio a ON a.id=NEW.conta_envio_id "
        "JOIN inscricoes_conta i ON i.id=NEW.inscricao_id JOIN identidades_telegram t "
        "ON t.id=NEW.identidade_telegram_id JOIN eventos_telegram e "
        "ON e.id=NEW.evento_telegram_id WHERE p.id=NEW.preparacao_id "
        "AND p.campanha_id=NEW.campanha_id AND p.confirmado_em IS NOT NULL "
        "AND p.invalidado_em IS NULL AND p.impressao=NEW.impressao "
        f"{unidade}AND c.status='preparada' AND c.conta_envio_id=NEW.conta_envio_id "
        "AND a.versao=NEW.conta_versao AND a.telegram_bot_id=NEW.bot_externo_id "
        "AND t.chat_externo_id=NEW.chat_externo_id "
        "AND i.conta_envio_id=NEW.conta_envio_id AND i.identidade_telegram_id=t.id "
        "AND i.estado='iniciada' AND e.inscricao_id=i.id AND e.conta_envio_id=a.id "
        "AND e.escopo_id=c.escopo_id AND e.categoria=c.categoria "
        "AND e.tipo='autorizou' AND e.confirmado=1 "
        "AND NOT EXISTS(SELECT 1 FROM eventos_telegram r "
        "WHERE r.inscricao_id=e.inscricao_id AND r.tipo='revogou' "
        "AND r.registrado_em>=e.registrado_em))",
        "Autorizacao Telegram incompativel",
    )
    for acao in ("UPDATE", "DELETE"):
        _recusar(
            f"autorizacao_envio_telegram_sem_{acao.lower()}",
            "campanha_autorizacoes_envio_telegram",
            f"BEFORE {acao}",
        )
    _recusar(
        "autorizacao_envio_telegram_sem_replace",
        "campanha_autorizacoes_envio_telegram",
        "BEFORE INSERT",
        "EXISTS(SELECT 1 FROM campanha_autorizacoes_envio_telegram "
        "WHERE preparacao_id=NEW.preparacao_id OR operacao_id=NEW.operacao_id)",
        "Registro existente",
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
        "Tentativa Telegram imutavel",
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
        "Envio Telegram preserva preparacao",
    )


def _reconstruir(*, lote):
    _remover_gatilhos()
    _criar_tabelas(lote=lote)
    _copiar(lote=lote)
    _substituir()
    _criar_gatilhos(lote=lote)


def upgrade():
    _reconstruir(lote=True)


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("""SELECT EXISTS(
        SELECT 1 FROM campanha_selecoes_telegram
        GROUP BY campanha_id HAVING COUNT(*)>1) OR EXISTS(
        SELECT 1 FROM campanha_preparacao_itens_telegram
        WHERE incluido=0 GROUP BY preparacao_id HAVING COUNT(*)>0) OR EXISTS(
        SELECT 1 FROM campanha_preparacao_itens_telegram
        GROUP BY preparacao_id HAVING COUNT(*)>1)""").scalar():
        raise RuntimeError("Downgrade recusado: preserve o lote Telegram.")
    _reconstruir(lote=False)
