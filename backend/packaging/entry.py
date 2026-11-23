"""Entrada PyInstaller; nenhum segredo, configuracao ou banco de build."""

import sys

# Alembic importa scripts fisicos; nao criar __pycache__ no bundle somente leitura.
sys.dont_write_bytecode = True

from app.__main__ import main  # noqa: E402 - desabilitar pycache antes de importar o launcher

if __name__ == "__main__":
    main()
