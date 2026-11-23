"""Cadastro independente de provedores."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText


class Registro:
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    ativo: Mapped[int] = mapped_column(Integer, server_default="1")
    versao: Mapped[int] = mapped_column(Integer, server_default="1")
    criado_em: Mapped[datetime] = mapped_column(UTCText())
    atualizado_em: Mapped[datetime] = mapped_column(UTCText())


def regras():
    return (
        CheckConstraint("length(id)=36", name="id_uuid"),
        CheckConstraint("ativo IN (0,1)", name="ativo"),
        CheckConstraint("versao >= 1", name="versao"),
        CheckConstraint("atualizado_em >= criado_em", name="datas"),
    )


class Contato(Registro, Base):
    __tablename__ = "contatos"
    __table_args__ = (
        *regras(),
        CheckConstraint("consentimento_versao >= 0", name="consentimento_versao"),
        CheckConstraint("nome IS NULL OR length(nome) BETWEEN 1 AND 120", name="nome"),
        CheckConstraint("observacoes IS NULL OR length(observacoes)<=2000", name="observacoes"),
        Index("ix_contatos_busca", "ativo", "nome_busca", "id"),
    )
    bloqueio_global_em: Mapped[datetime | None] = mapped_column(UTCText())
    consentimento_versao: Mapped[int] = mapped_column(Integer, server_default="0")
    nome: Mapped[str | None] = mapped_column(Text)
    nome_busca: Mapped[str] = mapped_column(Text, server_default="")
    observacoes: Mapped[str | None] = mapped_column(Text)


class TelefoneContato(Registro, Base):
    __tablename__ = "telefones_contato"
    __table_args__ = (
        *regras(),
        CheckConstraint(
            "length(telefone_e164) BETWEEN 3 AND 16 AND substr(telefone_e164,1,1)='+' "
            "AND substr(telefone_e164,2,1) BETWEEN '1' AND '9' "
            "AND substr(telefone_e164,2) NOT GLOB '*[^0-9]*'",
            name="e164",
        ),
        Index("uq_telefones_id_contato", "id", "contato_id", unique=True),
        Index("uq_telefones_e164", "telefone_e164", unique=True),
        Index("ix_telefones_contato", "contato_id", "ativo"),
    )
    contato_id: Mapped[str] = mapped_column(ForeignKey("contatos.id", ondelete="RESTRICT"))
    telefone_e164: Mapped[str] = mapped_column(Text)


class Lista(Registro, Base):
    __tablename__ = "listas"
    __table_args__ = (
        *regras(),
        CheckConstraint("length(nome) BETWEEN 1 AND 120", name="nome"),
        CheckConstraint("length(nome_normalizado) BETWEEN 1 AND 240", name="nome_normalizado"),
        CheckConstraint("descricao IS NULL OR length(descricao)<=2000", name="descricao"),
        Index("uq_listas_nome_normalizado", "nome_normalizado", unique=True),
    )
    nome: Mapped[str] = mapped_column(Text)
    nome_normalizado: Mapped[str] = mapped_column(Text)
    descricao: Mapped[str | None] = mapped_column(Text)


class ListaContato(Base):
    __tablename__ = "lista_contatos"
    lista_id: Mapped[str] = mapped_column(
        ForeignKey("listas.id", ondelete="RESTRICT"), primary_key=True
    )
    contato_id: Mapped[str] = mapped_column(
        ForeignKey("contatos.id", ondelete="RESTRICT"), primary_key=True
    )
    adicionado_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (Index("ix_lista_contatos_contato", "contato_id", "lista_id"),)


class Auditoria(Base):
    __tablename__ = "auditoria"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    usuario_id: Mapped[str | None] = mapped_column(ForeignKey("usuarios.id", ondelete="RESTRICT"))
    acao: Mapped[str] = mapped_column(Text)
    entidade: Mapped[str] = mapped_column(Text)
    entidade_id: Mapped[str] = mapped_column(Text)
    resumo_json: Mapped[str] = mapped_column(Text)
    ocorrido_em: Mapped[datetime] = mapped_column(UTCText())
    __table_args__ = (
        Index("ix_auditoria_entidade", "entidade", "entidade_id"),
        Index("ix_auditoria_data", "ocorrido_em"),
    )
