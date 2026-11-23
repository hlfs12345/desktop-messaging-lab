"""Persistencia operacional; fila referencia a fotografia, sem copiar PII."""

# ruff: noqa: E501

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.campanhas.models import _data_utc_canonica
from app.persistencia.models import Base, UTCText


def _datas(*colunas):
    return " AND ".join(f"({c} IS NULL OR {_data_utc_canonica(c)})" for c in colunas)


class ExecucaoCampanha(Base):
    __tablename__ = "campanha_execucoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    preparacao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_preparacoes.id", ondelete="RESTRICT"), unique=True
    )
    autorizacao_id: Mapped[str | None] = mapped_column(
        ForeignKey("campanha_autorizacoes_simulacao.id", ondelete="RESTRICT"), unique=True
    )
    autorizacao_telegram_id: Mapped[str | None] = mapped_column(
        ForeignKey("campanha_autorizacoes_envio_telegram.id", ondelete="RESTRICT"), unique=True
    )
    modo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    proprietario_id: Mapped[str] = mapped_column(Text)
    geracao: Mapped[int] = mapped_column(Integer)
    slot: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    alterado_por_usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        UniqueConstraint("id", "campanha_id", "preparacao_id"),
        CheckConstraint(
            "length(id)=36 AND length(proprietario_id)=36 AND typeof(versao)='integer' AND versao>=1 AND typeof(geracao)='integer' AND geracao>=1 AND slot=1",
            name="identidade",
        ),
        CheckConstraint(
            "estado IN ('executando','pausando','pausada','concluida','concluida_com_falhas','interrompida')",
            name="estado",
        ),
        CheckConstraint(
            "(modo='mock' AND autorizacao_id IS NOT NULL AND "
            "autorizacao_telegram_id IS NULL) OR (modo='telegram' AND "
            "autorizacao_id IS NULL AND autorizacao_telegram_id IS NOT NULL)",
            name="modo",
        ),
        CheckConstraint(
            _datas("criado_em", "atualizado_em") + " AND atualizado_em>=criado_em", name="datas"
        ),
        Index(
            "ix_execucoes_aberta",
            "campanha_id",
            unique=True,
            sqlite_where=text("estado IN ('executando','pausando','pausada')"),
        ),
        Index(
            "ix_execucoes_despachante",
            "slot",
            unique=True,
            sqlite_where=text("estado IN ('executando','pausando')"),
        ),
        Index("ix_execucoes_historico", "campanha_id", "criado_em", "id"),
    )


class DestinatarioCampanha(Base):
    __tablename__ = "campanha_destinatarios"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    campanha_id: Mapped[str] = mapped_column(ForeignKey("campanhas.id", ondelete="RESTRICT"))
    identidade_contato_id: Mapped[str | None] = mapped_column(
        ForeignKey("identidades_contato.id", ondelete="RESTRICT")
    )
    identidade_telegram_id: Mapped[str | None] = mapped_column(
        ForeignKey("identidades_telegram.id", ondelete="RESTRICT")
    )
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        UniqueConstraint("campanha_id", "identidade_contato_id"),
        UniqueConstraint("campanha_id", "identidade_telegram_id"),
        UniqueConstraint("id", "campanha_id", "identidade_contato_id"),
        UniqueConstraint("id", "campanha_id", "identidade_telegram_id"),
        CheckConstraint(
            "(identidade_contato_id IS NOT NULL) != "
            "(identidade_telegram_id IS NOT NULL)",
            name="canal",
        ),
    )


class ItemExecucao(Base):
    __tablename__ = "campanha_execucao_itens"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    execucao_id: Mapped[str] = mapped_column(Text)
    campanha_id: Mapped[str] = mapped_column(Text)
    preparacao_id: Mapped[str] = mapped_column(Text)
    destinatario_id: Mapped[str] = mapped_column(Text)
    identidade_contato_id: Mapped[str | None] = mapped_column(Text)
    identidade_telegram_id: Mapped[str | None] = mapped_column(Text)
    ordem: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    motivo_codigo: Mapped[str | None] = mapped_column(Text)
    alterado_por_usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        ForeignKeyConstraint(
            ["execucao_id", "campanha_id", "preparacao_id"],
            [
                "campanha_execucoes.id",
                "campanha_execucoes.campanha_id",
                "campanha_execucoes.preparacao_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["destinatario_id", "campanha_id", "identidade_contato_id"],
            [
                "campanha_destinatarios.id",
                "campanha_destinatarios.campanha_id",
                "campanha_destinatarios.identidade_contato_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["destinatario_id", "campanha_id", "identidade_telegram_id"],
            [
                "campanha_destinatarios.id",
                "campanha_destinatarios.campanha_id",
                "campanha_destinatarios.identidade_telegram_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["preparacao_id", "identidade_contato_id"],
            [
                "campanha_preparacao_itens.preparacao_id",
                "campanha_preparacao_itens.identidade_contato_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["preparacao_id", "identidade_telegram_id"],
            [
                "campanha_preparacao_itens_telegram.preparacao_id",
                "campanha_preparacao_itens_telegram.identidade_telegram_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "execucao_id"),
        UniqueConstraint("execucao_id", "destinatario_id", name="uq_execucao_itens_destinatario"),
        UniqueConstraint("execucao_id", "ordem", name="uq_execucao_itens_ordem"),
        CheckConstraint(
            "length(id)=36 AND typeof(ordem)='integer' AND ordem>=1 AND typeof(versao)='integer' AND versao>=1",
            name="identidade",
        ),
        CheckConstraint(
            "(identidade_contato_id IS NOT NULL) != "
            "(identidade_telegram_id IS NOT NULL)",
            name="canal",
        ),
        CheckConstraint(
            "estado IN ('pendente','reservado','processando','aceita_provedor','falhou','desconhecida','ignorado','interrompido')",
            name="estado",
        ),
        CheckConstraint(
            "(estado='ignorado' AND motivo_codigo IN "
            "('impedimento_atual','consentimento','sincronizacao')) OR "
            "(estado='interrompido' AND motivo_codigo='encerramento') OR "
            "(estado NOT IN ('ignorado','interrompido') AND motivo_codigo IS NULL)",
            name="motivo",
        ),
        CheckConstraint(_datas("atualizado_em"), name="datas"),
        Index("ix_execucao_itens_fila", "execucao_id", "estado", "ordem"),
    )


class TentativaEnvio(Base):
    __tablename__ = "tentativas_envio"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    execucao_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    numero: Mapped[int] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    proprietario_id: Mapped[str] = mapped_column(Text)
    geracao: Mapped[int] = mapped_column(Integer)
    slot: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    reservada_em: Mapped[datetime] = mapped_column(UTCText())
    invocacao_em: Mapped[datetime | None] = mapped_column(UTCText())
    finalizada_em: Mapped[datetime | None] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    codigo_resultado: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)
    retry_after: Mapped[int | None] = mapped_column(Integer)
    alterado_por_usuario_id: Mapped[str | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "execucao_id"],
            ["campanha_execucao_itens.id", "campanha_execucao_itens.execucao_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "item_id", "execucao_id"),
        UniqueConstraint("item_id", "numero"),
        CheckConstraint(
            "length(id)=36 AND length(proprietario_id)=36 AND typeof(versao)='integer' AND versao>=1 AND typeof(geracao)='integer' AND geracao>=1 AND typeof(numero)='integer' AND numero>=1 AND slot=1",
            name="identidade",
        ),
        CheckConstraint(
            "(estado='reservada' AND invocacao_em IS NULL AND finalizada_em IS NULL "
            "AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR "
            "(estado='iniciada' AND invocacao_em IS NOT NULL AND finalizada_em IS NULL "
            "AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR "
            "(estado='interrompida' AND invocacao_em IS NULL AND finalizada_em IS NOT NULL "
            "AND codigo_resultado IS NULL AND message_id IS NULL AND retry_after IS NULL) OR "
            "(estado='aceita_provedor' AND invocacao_em IS NOT NULL AND finalizada_em IS NOT NULL "
            "AND codigo_resultado='aceita' AND message_id IS NOT NULL "
            "AND retry_after IS NULL) OR "
            "(estado='falhou' AND invocacao_em IS NOT NULL AND finalizada_em IS NOT NULL "
            "AND codigo_resultado IS NOT NULL AND message_id IS NULL) OR "
            "(estado='desconhecida' AND invocacao_em IS NOT NULL AND finalizada_em IS NOT NULL "
            "AND codigo_resultado='sem_confirmacao' AND message_id IS NULL AND retry_after IS NULL)",
            name="resultado",
        ),
        CheckConstraint(
            "message_id IS NULL OR message_id>0", name="message_id"
        ),
        CheckConstraint(
            "retry_after IS NULL OR (codigo_resultado='limite_telegram' "
            "AND retry_after BETWEEN 1 AND 86400)",
            name="retry_after",
        ),
        CheckConstraint(
            _datas("reservada_em", "invocacao_em", "finalizada_em", "atualizado_em")
            + " AND atualizado_em>=reservada_em AND (invocacao_em IS NULL OR (invocacao_em>=reservada_em AND invocacao_em<=atualizado_em)) AND (finalizada_em IS NULL OR (finalizada_em>=COALESCE(invocacao_em,reservada_em) AND finalizada_em=atualizado_em))",
            name="datas",
        ),
        Index(
            "ix_tentativa_ativa_item",
            "item_id",
            unique=True,
            sqlite_where=text("estado IN ('reservada','iniciada')"),
        ),
        Index(
            "ix_tentativa_ativa_global",
            "slot",
            unique=True,
            sqlite_where=text("estado IN ('reservada','iniciada')"),
        ),
        Index("ix_tentativas_historico", "execucao_id", "item_id", "numero"),
    )


class ControleEnvioTelegram(Base):
    __tablename__ = "telegram_controle_envio"
    conta_envio_id: Mapped[str] = mapped_column(
        ForeignKey("contas_envio.id", ondelete="RESTRICT"), primary_key=True
    )
    liberar_em: Mapped[datetime | None] = mapped_column(UTCText())
    impedimento_codigo: Mapped[str | None] = mapped_column(Text)
    impedido_ate: Mapped[datetime | None] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint(
            "(impedimento_codigo IS NULL AND impedido_ate IS NULL) OR "
            "(impedimento_codigo='limite_telegram' AND impedido_ate IS NOT NULL) OR "
            "(impedimento_codigo='sincronizacao' AND impedido_ate IS NULL)",
            name="impedimento",
        ),
        CheckConstraint(
            _datas("liberar_em", "impedido_ate", "atualizado_em"), name="datas"
        ),
    )


class TransicaoExecucao(Base):
    __tablename__ = "campanha_execucao_transicoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    execucao_id: Mapped[str] = mapped_column(
        ForeignKey("campanha_execucoes.id", ondelete="RESTRICT")
    )
    item_id: Mapped[str | None] = mapped_column(Text)
    tentativa_id: Mapped[str | None] = mapped_column(Text)
    entidade: Mapped[str] = mapped_column(Text)
    origem: Mapped[str | None] = mapped_column(Text)
    destino: Mapped[str] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer)
    ocorrido_em: Mapped[datetime] = mapped_column(UTCText())
    usuario_id: Mapped[str | None] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "execucao_id"],
            ["campanha_execucao_itens.id", "campanha_execucao_itens.execucao_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tentativa_id", "item_id", "execucao_id"],
            ["tentativas_envio.id", "tentativas_envio.item_id", "tentativas_envio.execucao_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(id)=36 AND typeof(versao)='integer' AND versao>=1 AND ((versao=1 AND origem IS NULL) OR (versao>1 AND origem IS NOT NULL))",
            name="identidade",
        ),
        CheckConstraint(
            "(entidade='execucao' AND item_id IS NULL AND tentativa_id IS NULL) OR (entidade='item' AND item_id IS NOT NULL AND tentativa_id IS NULL) OR (entidade='tentativa' AND item_id IS NOT NULL AND tentativa_id IS NOT NULL)",
            name="entidade",
        ),
        CheckConstraint(_datas("ocorrido_em"), name="datas"),
        Index(
            "ix_transicoes_execucao_versao",
            "execucao_id",
            "versao",
            unique=True,
            sqlite_where=text("entidade='execucao'"),
        ),
        Index(
            "ix_transicoes_item_versao",
            "item_id",
            "versao",
            unique=True,
            sqlite_where=text("entidade='item'"),
        ),
        Index(
            "ix_transicoes_tentativa_versao",
            "tentativa_id",
            "versao",
            unique=True,
            sqlite_where=text("entidade='tentativa'"),
        ),
        Index("ix_transicoes_historico", "execucao_id", "ocorrido_em", "id"),
        Index(
            "ix_transicoes_entidade_versao",
            "execucao_id",
            "entidade",
            "item_id",
            "tentativa_id",
            "versao",
        ),
    )


class SuspensaoConsentimento(Base):
    """Intencao imutavel; confirmacao so depois de evento e bloqueio duraveis."""

    __tablename__ = "consentimento_suspensoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    escopo_id: Mapped[str] = mapped_column(
        ForeignKey("escopos_comunicacao.id", ondelete="RESTRICT")
    )
    contato_id: Mapped[str] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    identidade_contato_id: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    requisicao_hash: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    confirmado_em: Mapped[datetime | None] = mapped_column(UTCText())
    evento_id: Mapped[str | None] = mapped_column(
        ForeignKey("eventos_consentimento.id", ondelete="RESTRICT")
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["identidade_contato_id", "contato_id"],
            ["identidades_contato.id", "identidades_contato.contato_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(id)=36 AND length(requisicao_hash)=64", name="identidade"),
        CheckConstraint(
            "(tipo='bloqueio_global' AND identidade_contato_id IS NULL AND categoria IS NULL) OR "
            "(tipo='bloqueio_canal' AND identidade_contato_id IS NOT NULL AND categoria IS NULL) OR "
            "(tipo='revogou' AND identidade_contato_id IS NOT NULL AND categoria IS NOT NULL AND categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos'))",
            name="alvo",
        ),
        CheckConstraint(
            "(confirmado_em IS NULL AND evento_id IS NULL) OR "
            "(confirmado_em IS NOT NULL AND evento_id IS NOT NULL AND confirmado_em>=criado_em)",
            name="confirmacao",
        ),
        CheckConstraint(_datas("criado_em", "confirmado_em"), name="datas"),
        Index(
            "ix_suspensoes_pendentes",
            "contato_id",
            "escopo_id",
            sqlite_where=text("confirmado_em IS NULL"),
        ),
    )
