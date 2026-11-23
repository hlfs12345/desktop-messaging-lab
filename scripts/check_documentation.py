"""Verifica UTF-8 e corrupcao evidente nos documentos recuperados."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_text(text):
    if re.search(r"\u00c3[\u0080-\u00bf]|\u00c2[\u0080-\u00bf]|\u00e2\u20ac", text):
        raise ValueError("Conversao de codificacao inconsistente.")
    if "\ufffd" in text or re.search(r"[A-Za-z\u00c0-\u00ff]\?[A-Za-z\u00c0-\u00ff?]", text):
        raise ValueError("Corrupcao evidente de documentacao.")


def check():
    for name in ("PLANO_DESENVOLVIMENTO.md", "CONTEXTO_ATUAL.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        validate_text(text)
        for word in (
            "sqlite",
            "consentimento",
            "whatsapp",
            "migra\u00e7\u00e3o",
        ):
            if word not in text.lower():
                raise ValueError("Vocabulario recuperado ausente: " + word)


if __name__ == "__main__":
    check()
