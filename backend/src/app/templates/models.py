"""Catálogo mutável e conteúdo versionado imutável no SQLite."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    nome_busca: Mapped[str] = mapped_column(Text)
    descricao: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(Text)
    ativo: Mapped[int] = mapped_column(Integer)
    versao: Mapped[int] = mapped_column(Integer)
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("length(trim(nome)) BETWEEN 1 AND 120", name="nome"),
        CheckConstraint("length(nome_busca) BETWEEN 1 AND 240", name="busca"),
        CheckConstraint("descricao IS NULL OR length(descricao)<=2000", name="descricao"),
        CheckConstraint(
            "categoria IN ('promocoes','atualizacoes_solicitadas','eventos_grupos')",
            name="categoria",
        ),
        CheckConstraint("ativo IN (0,1) AND versao>=1", name="estado"),
        CheckConstraint("atualizado_em>=criado_em", name="datas"),
        Index("ix_templates_catalogo", "ativo", "categoria", "nome_busca", "id"),
    )


class TemplateVersao(Base):
    __tablename__ = "template_versoes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id", ondelete="RESTRICT"))
    plataforma: Mapped[str] = mapped_column(Text)
    numero_versao: Mapped[int] = mapped_column(Integer)
    conteudo: Mapped[str] = mapped_column(Text)
    variaveis_json: Mapped[str] = mapped_column(Text)
    criado_por_usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        CheckConstraint("length(id)=36", name="uuid"),
        CheckConstraint("plataforma IN ('whatsapp','telegram')", name="plataforma"),
        CheckConstraint("numero_versao>=1", name="numero"),
        CheckConstraint("length(trim(conteudo)) BETWEEN 1 AND 4000", name="conteudo"),
        CheckConstraint("variaveis_json IN ('[]','[\"nome\"]')", name="variaveis"),
        UniqueConstraint("template_id", "plataforma", "numero_versao"),
        Index("ix_template_versoes_plataforma", "plataforma", "template_id"),
    )
