"""Validação limitada e respostas públicas; senhas nunca aparecem no repr."""

import re
import unicodedata
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_login(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", value) is None:
        raise ValueError("Use de 3 a 64 letras sem acento, números, ponto, hífen ou sublinhado.")
    return value


def normalize_password(value: str) -> str:
    if len(value) > 1024:
        raise ValueError("Senha excede o tamanho permitido.")
    value = unicodedata.normalize("NFC", value)
    try:
        valid = 15 <= len(value) <= 128 and len(value.encode("utf-8")) <= 512
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError("Use de 15 a 128 caracteres na senha.")
    return value


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    login: str = Field(max_length=128)
    senha: str = Field(repr=False, max_length=1024)

    _login = field_validator("login")(normalize_login)
    _senha = field_validator("senha")(normalize_password)


class BootstrapInput(LoginInput):
    nome: str = Field(max_length=240)

    @field_validator("nome")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 120 or any(
            unicodedata.category(c).startswith("C") for c in value
        ):
            raise ValueError("Informe um nome de 1 a 120 caracteres.")
        return value


class PublicUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nome: str
    login: str
    perfil: str


class SessionView(BaseModel):
    usuario: PublicUser
    expira_em: datetime


class LoginResult(SessionView):
    token: str = Field(repr=False)
