"""Normalização internacional sem inferência silenciosa."""

import re
import unicodedata

import phonenumbers

from app.contatos.errors import NegocioError

VERSAO = "edisk-1/phonenumbers-" + phonenumbers.__version__


def busca(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)
    )


def texto(value: str | None, limite: int = 120) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFC", value.strip())
    if len(value) > limite or any(
        unicodedata.category(c).startswith("C") and c not in "\n\t" for c in value
    ):
        raise NegocioError("texto_invalido", "Texto inválido ou acima do limite permitido.")
    return value or None


def telefone(value: str, pais: str | None = None) -> str:
    if not isinstance(value, str) or len(value) > 80:
        raise NegocioError("telefone_invalido", "Informe um telefone válido como texto.")
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9 ()\-.]+", value):
        raise NegocioError(
            "telefone_invalido", "Use telefone sem ramal, letras ou múltiplos números."
        )
    region = pais.upper() if pais else None
    if region and region not in phonenumbers.SUPPORTED_REGIONS:
        raise NegocioError("pais_invalido", "Selecione um país válido.")
    if not value.startswith("+") and region is None:
        raise NegocioError("pais_obrigatorio", "Selecione o país para números nacionais.")
    digits = re.sub(r"\D", "", value)
    if region == "BR" and not value.startswith("+") and len(digits) not in (10, 11):
        raise NegocioError("telefone_ambiguo", "Informe DDD e número completos, ou + e país.")
    try:
        number = phonenumbers.parse(value, region)
        if number.extension or not phonenumbers.is_valid_number(number):
            raise ValueError
        if (
            not value.startswith("+")
            and region == "BR"
            and phonenumbers.national_significant_number(number) != digits
        ):
            raise ValueError
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException, ValueError:
        raise NegocioError(
            "telefone_invalido", "Telefone inválido ou ambíguo para o país."
        ) from None
