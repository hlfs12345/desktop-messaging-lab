"""Modelo mínimo; tabelas são criadas exclusivamente pelo Alembic."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText, utc_text  # noqa: F401

BOOTSTRAP_ID = "00000000-0000-0000-0000-000000000001"


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint("length(id) = 36", name="id_uuid"),
        CheckConstraint("length(nome) BETWEEN 1 AND 120", name="nome_tamanho"),
        CheckConstraint("length(login) BETWEEN 3 AND 64", name="login_tamanho"),
        CheckConstraint(
            "length(login_normalizado) BETWEEN 3 AND 64", name="login_normalizado_tamanho"
        ),
        CheckConstraint("perfil IN ('administrador', 'operador')", name="perfil"),
        CheckConstraint("ativo IN (0, 1)", name="ativo"),
        CheckConstraint("atualizado_em >= criado_em", name="datas"),
        Index("uq_usuarios_login_normalizado", "login_normalizado", unique=True),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    login: Mapped[str] = mapped_column(Text)
    login_normalizado: Mapped[str] = mapped_column(Text)
    senha_hash: Mapped[str] = mapped_column(Text)
    perfil: Mapped[str] = mapped_column(Text)
    ativo: Mapped[int] = mapped_column(Integer, server_default="1")
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())


class Sessao(Base):
    __tablename__ = "sessoes"
    __table_args__ = (
        CheckConstraint("length(id) = 36", name="id_uuid"),
        CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'", name="hash"
        ),
        CheckConstraint("expira_em > criado_em", name="expiracao"),
        CheckConstraint("revogado_em IS NULL OR revogado_em >= criado_em", name="revogacao"),
        Index("uq_sessoes_token_hash", "token_hash", unique=True),
        Index("ix_sessoes_usuario_expiracao", "usuario_id", "expira_em"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    token_hash: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    expira_em: Mapped[datetime] = mapped_column(UTCText())
    revogado_em: Mapped[datetime | None] = mapped_column(UTCText())


class EstadoAutenticacao(Base):
    __tablename__ = "estado_autenticacao"
    __table_args__ = (CheckConstraint(f"id = '{BOOTSTRAP_ID}'", name="unico"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    configurado_em: Mapped[datetime | None] = mapped_column(UTCText())
