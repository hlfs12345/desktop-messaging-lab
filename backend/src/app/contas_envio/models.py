"""Contas locais: nenhum identificador de destinatário ou segredo em claro."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistencia.models import Base, UTCText


class ContaEnvio(Base):
    __tablename__ = "contas_envio"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    nome_busca: Mapped[str] = mapped_column(Text)
    plataforma: Mapped[str] = mapped_column(Text)
    provedor: Mapped[str] = mapped_column(Text)
    ativo: Mapped[int] = mapped_column(Integer)
    removido_em: Mapped[datetime | None] = mapped_column(UTCText())
    segredo_ref: Mapped[str | None] = mapped_column(Text)
    telegram_bot_id: Mapped[str | None] = mapped_column(Text)
    telegram_proximo_offset: Mapped[int | None] = mapped_column(Integer)
    telegram_sincronizado_em: Mapped[datetime | None] = mapped_column(UTCText())
    telegram_lacuna_desde: Mapped[datetime | None] = mapped_column(UTCText())
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
            "(plataforma='whatsapp' AND provedor IN ('qr_candidato','meta_cloud','mock')) OR "
            "(plataforma='telegram' AND provedor='telegram_bot_api')",
            name="provedor",
        ),
        CheckConstraint("ativo IN (0,1) AND versao>=1", name="estado"),
        CheckConstraint(
            "removido_em IS NULL OR (ativo=0 AND segredo_ref IS NULL AND removido_em>=criado_em)",
            name="remocao",
        ),
        CheckConstraint(
            "segredo_ref IS NULL OR (length(segredo_ref)=32 AND "
            "segredo_ref NOT GLOB '*[^0-9a-f]*' AND provedor NOT IN ('qr_candidato','mock'))",
            name="referencia",
        ),
        CheckConstraint("atualizado_em>=criado_em", name="datas"),
        Index("ix_contas_envio_catalogo", "ativo", "nome_busca", "id"),
        Index("ix_contas_envio_provedor", "provedor", "removido_em"),
        Index("uq_contas_telegram_bot", "telegram_bot_id", unique=True),
        CheckConstraint(
            "telegram_proximo_offset IS NULL OR (telegram_bot_id IS NOT NULL AND "
            "typeof(telegram_proximo_offset)='integer' AND telegram_proximo_offset>=0)",
            name="telegram_offset",
        ),
        CheckConstraint(
            "(telegram_sincronizado_em IS NULL OR telegram_bot_id IS NOT NULL) AND "
            "(telegram_lacuna_desde IS NULL OR telegram_bot_id IS NOT NULL)",
            name="telegram_sincronizacao",
        ),
        CheckConstraint(
            "telegram_bot_id IS NULL OR (provedor='telegram_bot_api' AND "
            "length(telegram_bot_id) BETWEEN 1 AND 16 AND "
            "substr(telegram_bot_id,1,1) BETWEEN '1' AND '9' AND "
            "telegram_bot_id NOT GLOB '*[^0-9]*')",
            name="telegram_bot",
        ),
    )
