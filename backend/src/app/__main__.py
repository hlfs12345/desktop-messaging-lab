"""Launcher privado: stdin/stdout pertencem ao supervisor nativo."""

import argparse

from app.runtime.launcher import run


def parse_port(value: str) -> int:
    """Aceita uma porta TCP válida; zero solicita uma porta livre ao Windows."""
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("a porta deve ser um número inteiro") from error
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("a porta deve estar entre 0 e 65535")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(description="Inicia o backend do Messaging Lab em desenvolvimento.")
    parser.add_argument(
        "--port",
        type=parse_port,
        default=0,
        help="porta local; padrão: 0 (o sistema escolhe uma porta livre)",
    )
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.port))


if __name__ == "__main__":
    main()
