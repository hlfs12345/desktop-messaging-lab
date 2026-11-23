"""Consentimento local independente de conectores."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText

CATEGORIAS = ("promocoes", "atualizacoes_solicitadas", "eventos_grupos")
CATEGORIAS_SQL = "'promocoes','atualizacoes_solicitadas','eventos_grupos'"


class EscopoComunicacao(Base):
    __tablename__ = "escopos_comunicacao"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    singleton: Mapped[int] = mapped_column(Integer, default=1)
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("length(trim(nome)) BETWEEN 1 AND 120", name="nome"),
        CheckConstraint("singleton=1", name="singleton"),
        UniqueConstraint("singleton"),
    )


class IdentidadeContato(Base):
    __tablename__ = "identidades_contato"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    contato_id: Mapped[str] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    telefone_id: Mapped[str] = mapped_column(Text)
    canal: Mapped[str] = mapped_column(Text)
    bloqueio_canal_em: Mapped[datetime | None] = mapped_column(UTCText())
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("canal='whatsapp'", name="canal"),
        ForeignKeyConstraint(
            ["telefone_id", "contato_id"],
            ["telefones_contato.id", "telefones_contato.contato_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("telefone_id", "canal"),
        UniqueConstraint("id", "contato_id"),
        Index("ix_identidades_contato", "contato_id", "canal"),
    )


class EventoConsentimento(Base):
    __tablename__ = "eventos_consentimento"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    escopo_id: Mapped[str] = mapped_column(
        ForeignKey("escopos_comunicacao.id", ondelete="RESTRICT")
    )
    contato_id: Mapped[str] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    identidade_contato_id: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text)
    ocorrido_em: Mapped[datetime] = mapped_column(UTCText())
    registrado_em: Mapped[datetime] = mapped_column(UTCText())
    metodo_captura: Mapped[str | None] = mapped_column(Text)
    evidencia_referencia: Mapped[str | None] = mapped_column(Text)
    justificativa: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    requisicao_hash: Mapped[str] = mapped_column(Text)
    versao_resultante: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("length(id)=36 AND length(requisicao_hash)=64", name="identificadores"),
        CheckConstraint("origem='operador' AND versao_resultante>=1", name="origem_versao"),
        CheckConstraint("ocorrido_em <= registrado_em", name="datas"),
        CheckConstraint(
            "(tipo IN ('autorizou','revogou') AND identidade_contato_id IS NOT NULL "
            f"AND categoria IS NOT NULL AND categoria IN ({CATEGORIAS_SQL})) OR "
            "(tipo IN ('bloqueio_canal','desbloqueio_canal') "
            "AND identidade_contato_id IS NOT NULL AND categoria IS NULL) OR "
            "(tipo IN ('bloqueio_global','desbloqueio_global') "
            "AND identidade_contato_id IS NULL AND categoria IS NULL)",
            name="alvo",
        ),
        CheckConstraint(
            "tipo!='autorizou' OR (metodo_captura IS NOT NULL "
            "AND length(trim(metodo_captura)) BETWEEN 1 AND 120 "
            "AND evidencia_referencia IS NOT NULL "
            "AND length(trim(evidencia_referencia)) BETWEEN 1 AND 500)",
            name="evidencia",
        ),
        CheckConstraint(
            "tipo='autorizou' OR (justificativa IS NOT NULL "
            "AND length(trim(justificativa)) BETWEEN 1 AND 500)",
            name="justificativa",
        ),
        CheckConstraint(
            "(metodo_captura IS NULL OR length(metodo_captura)<=120) AND "
            "(evidencia_referencia IS NULL OR length(evidencia_referencia)<=500) AND "
            "(justificativa IS NULL OR length(justificativa)<=500)",
            name="tamanhos",
        ),
        ForeignKeyConstraint(
            ["identidade_contato_id", "contato_id"],
            ["identidades_contato.id", "identidades_contato.contato_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "escopo_id"),
        Index("ix_eventos_consentimento_historico", "contato_id", "registrado_em", "id"),
    )


class PreferenciaContato(Base):
    __tablename__ = "preferencias_contato"
    escopo_id: Mapped[str] = mapped_column(
        ForeignKey("escopos_comunicacao.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    identidade_contato_id: Mapped[str] = mapped_column(
        ForeignKey("identidades_contato.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    categoria: Mapped[str] = mapped_column(Text, primary_key=True)
    estado: Mapped[str] = mapped_column(Text)
    evento_origem_id: Mapped[str] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint(f"categoria IN ({CATEGORIAS_SQL})", name="categoria"),
        CheckConstraint("estado IN ('desconhecido','autorizado','bloqueado')", name="estado"),
        ForeignKeyConstraint(
            ["evento_origem_id", "escopo_id"],
            ["eventos_consentimento.id", "eventos_consentimento.escopo_id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_preferencias_filtro", "escopo_id", "categoria", "estado", "identidade_contato_id"
        ),
    )


class IdentidadeTelegram(Base):
    """Identidade privada sem telefone; referências externas são opacas ao núcleo."""

    __tablename__ = "identidades_telegram"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    contato_id: Mapped[str] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    usuario_externo_id: Mapped[str] = mapped_column(Text, unique=True)
    chat_externo_id: Mapped[str] = mapped_column(Text, unique=True)
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint(
            "usuario_externo_id=chat_externo_id AND "
            "length(usuario_externo_id) BETWEEN 1 AND 16 AND "
            "substr(usuario_externo_id,1,1) BETWEEN '1' AND '9' AND "
            "usuario_externo_id NOT GLOB '*[^0-9]*'",
            name="chat_privado",
        ),
        Index("ix_identidades_telegram_contato", "contato_id"),
    )


class InscricaoConta(Base):
    __tablename__ = "inscricoes_conta"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    identidade_telegram_id: Mapped[str] = mapped_column(
        ForeignKey("identidades_telegram.id", ondelete="RESTRICT")
    )
    estado: Mapped[str] = mapped_column(Text)
    registrado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("estado IN ('iniciada','revogada')", name="estado"),
        CheckConstraint("atualizado_em>=registrado_em", name="datas"),
        UniqueConstraint("conta_envio_id", "identidade_telegram_id"),
        UniqueConstraint("id", "conta_envio_id"),
        Index("ix_inscricoes_id_identidade", "id", "identidade_telegram_id", unique=True),
    )


class EventoTelegram(Base):
    """Interação e evidência imutáveis; aceite sempre limitado à conta e categoria."""

    __tablename__ = "eventos_telegram"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    inscricao_id: Mapped[str] = mapped_column(Text)
    conta_envio_id: Mapped[str] = mapped_column(Text)
    escopo_id: Mapped[str] = mapped_column(
        ForeignKey("escopos_comunicacao.id", ondelete="RESTRICT")
    )
    evento_externo_id: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(Text)
    aviso_versao: Mapped[str | None] = mapped_column(Text)
    aviso_texto: Mapped[str | None] = mapped_column(Text)
    confirmado: Mapped[int] = mapped_column(Integer)
    ocorrido_em: Mapped[datetime] = mapped_column(UTCText())
    registrado_em: Mapped[datetime] = mapped_column(UTCText())
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    requisicao_hash: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("length(id)=36 AND length(requisicao_hash)=64", name="ids"),
        CheckConstraint("length(trim(evento_externo_id)) BETWEEN 1 AND 100", name="externo"),
        CheckConstraint("ocorrido_em<=registrado_em", name="datas"),
        CheckConstraint(
            f"(tipo='autorizou' AND categoria IN ({CATEGORIAS_SQL}) AND categoria IS NOT NULL "
            "AND confirmado=1 AND aviso_versao IS NOT NULL AND aviso_texto IS NOT NULL "
            "AND length(trim(aviso_versao)) BETWEEN 1 AND 80 "
            "AND length(trim(aviso_texto)) BETWEEN 1 AND 2000) OR "
            "(tipo IN ('iniciou','revogou') AND categoria IS NULL AND confirmado=0 "
            "AND aviso_versao IS NULL AND aviso_texto IS NULL)",
            name="finalidade",
        ),
        ForeignKeyConstraint(
            ["inscricao_id", "conta_envio_id"],
            ["inscricoes_conta.id", "inscricoes_conta.conta_envio_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("conta_envio_id", "evento_externo_id"),
        Index("ix_eventos_telegram_finalidade", "inscricao_id", "escopo_id", "categoria"),
        Index("ix_eventos_telegram_aviso", "conta_envio_id", "categoria", "aviso_versao"),
    )


class ReciboTelegram(Base):
    __tablename__ = "telegram_recibos"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conta_envio_id: Mapped[str] = mapped_column(ForeignKey("contas_envio.id", ondelete="RESTRICT"))
    update_id: Mapped[int] = mapped_column(Integer)
    resultado: Mapped[str] = mapped_column(Text)
    registrado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("typeof(update_id)='integer' AND update_id>=0", name="update_id"),
        CheckConstraint(
            "resultado IN ('iniciou','autorizou','revogou','ignorada')", name="resultado"
        ),
        UniqueConstraint("conta_envio_id", "update_id"),
        Index("ix_telegram_recibos_data", "conta_envio_id", "registrado_em"),
    )
