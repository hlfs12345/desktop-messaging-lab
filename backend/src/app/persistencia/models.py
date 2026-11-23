"""Metadata e datas UTC compartilhadas; criação somente por Alembic."""

from datetime import UTC, datetime

from sqlalchemy import MetaData, Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Data deve possuir fuso horário.")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class UTCText(TypeDecorator[datetime]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return utc_text(value) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if utc_text(parsed) != value:
            raise ValueError("Data persistida inválida.")
        return parsed


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
