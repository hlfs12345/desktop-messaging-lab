"""Metadados minimizados; arquivo não persiste."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText


class Importacao(Base):
    __tablename__ = "importacoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    nome_arquivo: Mapped[str] = mapped_column(Text)
    hash_arquivo: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    configuracao_json: Mapped[str] = mapped_column(Text, server_default="{}")
    normalizacao_versao: Mapped[str] = mapped_column(Text)
    contagens_json: Mapped[str] = mapped_column(Text, server_default="{}")
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    concluido_em: Mapped[datetime | None] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="id_uuid"),
        CheckConstraint("length(nome_arquivo) BETWEEN 1 AND 180", name="arquivo"),
        CheckConstraint(
            "hash_arquivo IS NULL OR (length(hash_arquivo)=64 "
            "AND hash_arquivo NOT GLOB '*[^0-9a-f]*')",
            name="hash",
        ),
        CheckConstraint(
            "status IN ('criada','carregada','validando','pronta','confirmada',"
            "'cancelada','interrompida','falhou','expirada')",
            name="status",
        ),
        Index("ix_importacoes_data", "criado_em", "id"),
        Index("ix_importacoes_usuario", "usuario_id", "status"),
    )


class ImportacaoLinha(Base):
    __tablename__ = "importacao_linhas"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    importacao_id: Mapped[str] = mapped_column(ForeignKey("importacoes.id", ondelete="RESTRICT"))
    numero_linha: Mapped[int] = mapped_column(Integer)
    resultado: Mapped[str] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    contato_id: Mapped[str | None] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    telefone_id: Mapped[str | None] = mapped_column(
        ForeignKey("telefones_contato.id", ondelete="RESTRICT")
    )
    __table_args__ = (
        CheckConstraint("numero_linha>=1", name="linha"),
        CheckConstraint(
            "resultado IN ('inserido','atualizado','sem_alteracao',"
            "'duplicado','invalido','ignorado')",
            name="resultado",
        ),
        Index("uq_importacao_linha", "importacao_id", "numero_linha", unique=True),
        Index("ix_importacao_resultado", "importacao_id", "resultado", "numero_linha"),
    )
