"""Persistência restrita ao rascunho e à seleção explícita."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText


class Campanha(Base):
    __tablename__ = "campanhas"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    nome_busca: Mapped[str] = mapped_column(Text)
    conta_envio_id: Mapped[str] = mapped_column(
        ForeignKey("contas_envio.id", ondelete="RESTRICT")
    )
    escopo_id: Mapped[str] = mapped_column(
        ForeignKey("escopos_comunicacao.id", ondelete="RESTRICT")
    )
    template_versao_id: Mapped[str] = mapped_column(
        ForeignKey("template_versoes.id", ondelete="RESTRICT")
    )
    categoria: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("length(trim(nome)) BETWEEN 1 AND 120", name="nome"),
        CheckConstraint("length(nome_busca) BETWEEN 1 AND 240", name="busca"),
        CheckConstraint(
            "categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos')",
            name="categoria",
        ),
        CheckConstraint("status IN ('rascunho','preparada') AND versao>=1", name="estado"),
        CheckConstraint("atualizado_em>=criado_em", name="datas"),
        Index("ix_campanhas_catalogo", "nome_busca", "id"),
        Index("ix_campanhas_conta", "conta_envio_id", "criado_em"),
    )


class CampanhaLista(Base):
    __tablename__ = "campanha_listas"
    campanha_id: Mapped[str] = mapped_column(
        ForeignKey("campanhas.id", ondelete="RESTRICT"), primary_key=True
    )
    lista_id: Mapped[str] = mapped_column(
        ForeignKey("listas.id", ondelete="RESTRICT"), primary_key=True
    )


class CampanhaSelecao(Base):
    __tablename__ = "campanha_selecoes"
    campanha_id: Mapped[str] = mapped_column(
        ForeignKey("campanhas.id", ondelete="RESTRICT"), primary_key=True
    )
    identidade_contato_id: Mapped[str] = mapped_column(
        ForeignKey("identidades_contato.id", ondelete="RESTRICT"), primary_key=True
    )
    selecionado_em: Mapped[datetime] = mapped_column(UTCText())


class CampanhaSelecaoTelegram(Base):
    """Destino Telegram explícito; uma campanha usa no máximo uma inscrição."""

    __tablename__ = "campanha_selecoes_telegram"
    campanha_id: Mapped[str] = mapped_column(
        ForeignKey("campanhas.id", ondelete="RESTRICT"), primary_key=True
    )
    conta_envio_id: Mapped[str] = mapped_column(
        ForeignKey("contas_envio.id", ondelete="RESTRICT")
    )
    identidade_telegram_id: Mapped[str] = mapped_column(Text, primary_key=True)
    inscricao_id: Mapped[str] = mapped_column(Text)
    selecionado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        ForeignKeyConstraint(
            ["inscricao_id", "conta_envio_id"],
            ["inscricoes_conta.id", "inscricoes_conta.conta_envio_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["inscricao_id", "identidade_telegram_id"],
            ["inscricoes_conta.id", "inscricoes_conta.identidade_telegram_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(campanha_id)=36", name="campanha_uuid"),
    )


class ContextoCampanhas(Base):
    __tablename__ = "campanha_contexto_criptografico"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    base_id: Mapped[str] = mapped_column(Text, unique=True)
    chave_dpapi: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    verificador: Mapped[bytes] = mapped_column(LargeBinary)
    conferido: Mapped[int] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("id='campanhas-v1' AND length(base_id)=36", name="identidade"),
        CheckConstraint(
            (
                "length(chave_dpapi) BETWEEN 1 AND 65536 AND length(nonce)=12 AND "
                "length(verificador)=48"
            ),
            name="blobs",
        ),
        CheckConstraint("conferido IN (0,1)", name="conferencia"),
    )


class Preparacao(Base):
    __tablename__ = "campanha_preparacoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    base_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_contexto_criptografico.base_id", ondelete="RESTRICT")
    )
    versao_origem: Mapped[int] = mapped_column(Integer)
    revisao_pos: Mapped[int] = mapped_column(Integer)
    fotografia_json: Mapped[str] = mapped_column(Text)
    chave_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    chave_cifrada: Mapped[bytes] = mapped_column(LargeBinary)
    manifesto_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    manifesto: Mapped[bytes] = mapped_column(LargeBinary)
    impressao: Mapped[str] = mapped_column(Text)
    incluidos: Mapped[int] = mapped_column(Integer)
    excluidos: Mapped[int] = mapped_column(Integer)
    bytes_texto: Mapped[int] = mapped_column(Integer)
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    confirmado_por_usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    confirmado_em: Mapped[datetime | None] = mapped_column(UTCText())
    invalidado_em: Mapped[datetime | None] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36 AND versao_origem>=1 AND revisao_pos>=1", name="identidade"),
        CheckConstraint(
            (
                "incluidos>=1 AND excluidos>=0 AND incluidos+excluidos<=10000 AND "
                "bytes_texto BETWEEN 1 AND 67108864"
            ),
            name="volume",
        ),
        CheckConstraint(
            (
                "length(chave_nonce)=12 AND length(chave_cifrada)=48 AND "
                "length(manifesto_nonce)=12 AND length(manifesto)=48 AND "
                "length(impressao)=64"
            ),
            name="cifras",
        ),
        CheckConstraint(
            (
                "(confirmado_em IS NULL AND confirmado_por_usuario_id IS NULL) OR "
                "(confirmado_em>=criado_em AND confirmado_por_usuario_id IS NOT "
                "NULL)"
            ),
            name="confirmacao",
        ),
        CheckConstraint("invalidado_em IS NULL OR invalidado_em>=criado_em", name="invalidacao"),
        Index(
            "ix_preparacoes_vigente",
            "campanha_id",
            unique=True,
            sqlite_where=text("invalidado_em IS NULL"),
        ),
        Index("ix_preparacoes_historico", "campanha_id", "criado_em"),
        Index("ix_preparacoes_nonce", "base_id", "chave_nonce", unique=True),
    )


class PreparacaoItem(Base):
    __tablename__ = "campanha_preparacao_itens"
    preparacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_preparacoes.id", ondelete="RESTRICT"), primary_key=True
    )
    identidade_contato_id: Mapped[str] = mapped_column(
        ForeignKey("identidades_contato.id", ondelete="RESTRICT"), primary_key=True
    )
    incluido: Mapped[int] = mapped_column(Integer)
    motivos_json: Mapped[str] = mapped_column(Text)
    evento_consentimento_id: Mapped[str | None] = mapped_column(
        ForeignKey("eventos_consentimento.id", ondelete="RESTRICT")
    )
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    conteudo_cifrado: Mapped[bytes | None] = mapped_column(LargeBinary)
    bytes_texto: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint(
            (
                "(incluido=1 AND nonce IS NOT NULL AND conteudo_cifrado IS NOT "
                "NULL AND length(nonce)=12 AND "
                "length(conteudo_cifrado)=bytes_texto+16 AND bytes_texto BETWEEN 1"
                " AND 65536 AND motivos_json='[]') OR (incluido=0 AND nonce IS "
                "NULL AND conteudo_cifrado IS NULL AND bytes_texto=0 AND "
                "motivos_json!='[]')"
            ),
            name="conteudo",
        ),
        Index("ix_preparacao_itens_pagina", "preparacao_id", "incluido", "identidade_contato_id"),
        Index("ix_preparacao_itens_nonce", "preparacao_id", "nonce", unique=True),
    )


class PreparacaoItemTelegram(Base):
    """Item autenticado único para preparação Telegram."""

    __tablename__ = "campanha_preparacao_itens_telegram"
    preparacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_preparacoes.id", ondelete="RESTRICT"), primary_key=True
    )
    conta_envio_id: Mapped[str] = mapped_column(Text)
    identidade_telegram_id: Mapped[str] = mapped_column(
        ForeignKey("identidades_telegram.id", ondelete="RESTRICT"), primary_key=True
    )
    inscricao_id: Mapped[str] = mapped_column(Text)
    incluido: Mapped[int] = mapped_column(Integer)
    motivos_json: Mapped[str] = mapped_column(Text)
    evento_telegram_id: Mapped[str | None] = mapped_column(
        ForeignKey("eventos_telegram.id", ondelete="RESTRICT")
    )
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    conteudo_cifrado: Mapped[bytes | None] = mapped_column(LargeBinary)
    bytes_texto: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["inscricao_id", "conta_envio_id"],
            ["inscricoes_conta.id", "inscricoes_conta.conta_envio_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["inscricao_id", "identidade_telegram_id"],
            ["inscricoes_conta.id", "inscricoes_conta.identidade_telegram_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(incluido=1 AND evento_telegram_id IS NOT NULL AND nonce IS NOT NULL "
            "AND conteudo_cifrado IS NOT NULL AND length(nonce)=12 AND "
            "length(conteudo_cifrado)=bytes_texto+16 AND bytes_texto BETWEEN 1 "
            "AND 65536 AND motivos_json='[]') OR (incluido=0 AND nonce IS NULL "
            "AND conteudo_cifrado IS NULL AND bytes_texto=0 AND motivos_json!='[]')",
            name="conteudo",
        ),
        Index(
            "ix_preparacao_telegram_identidade",
            "identidade_telegram_id",
            "preparacao_id",
        ),
        Index("ix_preparacao_telegram_nonce", "preparacao_id", "nonce", unique=True),
        Index(
            "uq_preparacao_telegram_vinculo",
            "preparacao_id",
            "conta_envio_id",
            "identidade_telegram_id",
            "inscricao_id",
            unique=True,
        ),
    )


class AutorizacaoEnvioTelegram(Base):
    """Confirmacao humana imutavel, separada da simulacao e do consentimento."""

    __tablename__ = "campanha_autorizacoes_envio_telegram"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    operacao_id: Mapped[str] = mapped_column(Text, unique=True)
    requisicao_hash: Mapped[str] = mapped_column(Text)
    modo: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    preparacao_id: Mapped[str] = mapped_column(Text, unique=True)
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    identidade_telegram_id: Mapped[str | None] = mapped_column(Text)
    inscricao_id: Mapped[str | None] = mapped_column(Text)
    evento_telegram_id: Mapped[str | None] = mapped_column(
        ForeignKey("eventos_telegram.id", ondelete="RESTRICT")
    )
    bot_externo_id: Mapped[str] = mapped_column(Text)
    chat_externo_id: Mapped[str | None] = mapped_column(Text)
    conta_versao: Mapped[int] = mapped_column(Integer)
    impressao: Mapped[str] = mapped_column(Text)
    conteudo_hash: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(Text)
    quantidade: Mapped[int | None] = mapped_column(Integer)
    configuracao_hash: Mapped[str | None] = mapped_column(Text)
    intervalo_segundos: Mapped[int | None] = mapped_column(Integer)
    autorizado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    autorizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        ForeignKeyConstraint(
            ["preparacao_id", "conta_envio_id", "identidade_telegram_id", "inscricao_id"],
            [
                "campanha_preparacao_itens_telegram.preparacao_id",
                "campanha_preparacao_itens_telegram.conta_envio_id",
                "campanha_preparacao_itens_telegram.identidade_telegram_id",
                "campanha_preparacao_itens_telegram.inscricao_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(id)=36 AND length(operacao_id)=36 AND length(requisicao_hash)=64 "
            "AND versao>=1 AND modo IN ('unitario','lote')",
            name="ids",
        ),
        CheckConstraint(
            "length(impressao)=64 AND length(conteudo_hash)=64 AND conta_versao>=1",
            name="fotografia",
        ),
        CheckConstraint(
            "(modo='unitario' AND identidade_telegram_id IS NOT NULL AND "
            "inscricao_id IS NOT NULL AND evento_telegram_id IS NOT NULL AND "
            "chat_externo_id IS NOT NULL AND categoria IS NULL AND quantidade IS NULL "
            "AND configuracao_hash IS NULL AND intervalo_segundos IS NULL) OR "
            "(modo='lote' AND identidade_telegram_id IS NULL AND inscricao_id IS NULL "
            "AND evento_telegram_id IS NULL AND chat_externo_id IS NULL AND "
            "categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos') "
            "AND quantidade>=1 AND length(configuracao_hash)=64 "
            "AND intervalo_segundos>=5)",
            name="modo",
        ),
        Index("ix_autorizacoes_envio_telegram_campanha", "campanha_id", "autorizado_em"),
    )


class TentativaEnvioTelegram(Base):
    __tablename__ = "campanha_tentativas_envio_telegram"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    autorizacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_autorizacoes_envio_telegram.id", ondelete="RESTRICT"), unique=True
    )
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    chat_externo_id: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text)
    codigo_resultado: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)
    retry_after: Mapped[int | None] = mapped_column(Integer)
    iniciada_em: Mapped[datetime] = mapped_column(UTCText())
    finalizada_em: Mapped[datetime | None] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint(
            "estado IN ('iniciada','confirmada','falhou','desconhecida')", name="estado"
        ),
        CheckConstraint(
            "(estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL "
            "AND retry_after IS NULL AND finalizada_em IS NULL) OR "
            "(estado='confirmada' AND codigo_resultado='aceita' AND message_id>0 "
            "AND retry_after IS NULL AND finalizada_em IS NOT NULL) OR "
            "(estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL "
            "AND finalizada_em IS NOT NULL) OR "
            "(estado='desconhecida' AND codigo_resultado='sem_confirmacao' "
            "AND message_id IS NULL AND retry_after IS NULL AND finalizada_em IS NOT NULL)",
            name="resultado",
        ),
        CheckConstraint(
            "retry_after IS NULL OR (codigo_resultado='limite_telegram' "
            "AND retry_after BETWEEN 1 AND 86400)",
            name="limite",
        ),
    )


class AutorizacaoEnvioWhatsapp(Base):
    """Confirmacao humana imutavel para uma mensagem WhatsApp."""

    __tablename__ = "campanha_autorizacoes_envio_whatsapp"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    operacao_id: Mapped[str] = mapped_column(Text, unique=True)
    requisicao_hash: Mapped[str] = mapped_column(Text)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    preparacao_id: Mapped[str] = mapped_column(Text, unique=True)
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    identidade_contato_id: Mapped[str] = mapped_column(Text)
    evento_consentimento_id: Mapped[str] = mapped_column(
        ForeignKey("eventos_consentimento.id", ondelete="RESTRICT")
    )
    destino_e164: Mapped[str] = mapped_column(Text)
    conta_versao: Mapped[int] = mapped_column(Integer)
    impressao: Mapped[str] = mapped_column(Text)
    conteudo_hash: Mapped[str] = mapped_column(Text)
    autorizado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    autorizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        ForeignKeyConstraint(
            ["preparacao_id", "identidade_contato_id"],
            [
                "campanha_preparacao_itens.preparacao_id",
                "campanha_preparacao_itens.identidade_contato_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(id)=36 AND length(operacao_id)=36 AND length(requisicao_hash)=64",
            name="ids",
        ),
        CheckConstraint(
            "length(impressao)=64 AND length(conteudo_hash)=64 AND conta_versao>=1",
            name="fotografia",
        ),
        CheckConstraint(
            "length(destino_e164) BETWEEN 3 AND 16 AND substr(destino_e164,1,1)='+' "
            "AND substr(destino_e164,2,1) BETWEEN '1' AND '9' "
            "AND substr(destino_e164,2) NOT GLOB '*[^0-9]*'",
            name="destino",
        ),
        Index("ix_autorizacoes_envio_whatsapp_campanha", "campanha_id", "autorizado_em"),
    )


class TentativaEnvioWhatsapp(Base):
    __tablename__ = "campanha_tentativas_envio_whatsapp"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    autorizacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_autorizacoes_envio_whatsapp.id", ondelete="RESTRICT"),
        unique=True,
    )
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    estado: Mapped[str] = mapped_column(Text)
    codigo_resultado: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    iniciada_em: Mapped[datetime] = mapped_column(UTCText())
    finalizada_em: Mapped[datetime | None] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint(
            "estado IN ('iniciada','confirmada','falhou','desconhecida')", name="estado"
        ),
        CheckConstraint(
            "(estado='iniciada' AND codigo_resultado IS NULL AND message_id IS NULL "
            "AND finalizada_em IS NULL) OR "
            "(estado='confirmada' AND codigo_resultado='aceita' "
            "AND length(message_id) BETWEEN 1 AND 256 AND finalizada_em IS NOT NULL) OR "
            "(estado='falhou' AND codigo_resultado IS NOT NULL AND message_id IS NULL "
            "AND finalizada_em IS NOT NULL) OR "
            "(estado='desconhecida' AND codigo_resultado='sem_confirmacao' "
            "AND message_id IS NULL AND finalizada_em IS NOT NULL)",
            name="resultado",
        ),
    )


def _data_utc_canonica(coluna):
    # Normalizar so o calendario evita perda/arredondamento dos microssegundos.
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


class AutorizacaoSimulacao(Base):
    """Decisao historica; nao representa elegibilidade atual nem inicia processamento."""

    __tablename__ = "campanha_autorizacoes_simulacao"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    preparacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_preparacoes.id", ondelete="RESTRICT"), unique=True
    )
    base_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_contexto_criptografico.base_id", ondelete="RESTRICT")
    )
    impressao: Mapped[str] = mapped_column(Text)
    resumo_configuracao: Mapped[str] = mapped_column(Text)
    autorizado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    autorizado_em: Mapped[datetime] = mapped_column(UTCText())
    estado: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    revogado_por_usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    revogado_em: Mapped[datetime | None] = mapped_column(UTCText())
    motivo_codigo: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint(
            "length(impressao)=64 AND impressao NOT GLOB '*[^0-9a-f]*' AND "
            "length(resumo_configuracao)=64 AND resumo_configuracao NOT GLOB '*[^0-9a-f]*'",
            name="resumos",
        ),
        CheckConstraint("estado IN ('vigente','revogada')", name="estado"),
        CheckConstraint("typeof(versao)='integer' AND versao>=1", name="versao"),
        CheckConstraint(
            f"{_data_utc_canonica('autorizado_em')} AND "
            f"(revogado_em IS NULL OR ({_data_utc_canonica('revogado_em')} AND "
            "revogado_em>=autorizado_em))",
            name="datas",
        ),
        CheckConstraint(
            "(estado='vigente' AND revogado_por_usuario_id IS NULL AND "
            "revogado_em IS NULL AND motivo_codigo IS NULL) OR "
            "(estado='revogada' AND revogado_por_usuario_id IS NOT NULL AND "
            "revogado_em IS NOT NULL AND motivo_codigo IS NOT NULL AND "
            "motivo_codigo IN ('revogacao_manual','retorno_rascunho'))",
            name="revogacao",
        ),
        Index(
            "ix_autorizacoes_simulacao_vigente",
            "campanha_id",
            unique=True,
            sqlite_where=text("estado='vigente'"),
        ),
        Index("ix_autorizacoes_simulacao_historico", "campanha_id", "autorizado_em", "id"),
    )
