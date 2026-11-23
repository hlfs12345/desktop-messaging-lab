"""Caminhos por ambiente, sem efeitos de I/O durante a importação."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.persistencia.errors import ConfigurationError

Environment = Literal["development", "production", "test"]


def _absolute(path: Path) -> Path:
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise ConfigurationError("Os dados exigem um caminho local absoluto.")
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    environment: Environment
    root: Path

    def __post_init__(self) -> None:
        if self.environment not in {"development", "production", "test"}:
            raise ConfigurationError("Ambiente de execução inválido.")
        root = _absolute(self.root)
        source_root = Path(__file__).resolve().parents[4]
        forbidden = [source_root]
        if getattr(sys, "frozen", False):
            forbidden.append(Path(sys.executable).resolve().parent)
        if any(root == path or root.is_relative_to(path) for path in forbidden):
            raise ConfigurationError("Os dados devem ficar fora do código e da instalação.")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            production = _absolute(Path(local)) / "eDisk"
            expected = (
                production if self.environment == "production" else production / "development"
            )
            if self.environment == "test":
                if root.is_relative_to(production) or production.is_relative_to(root):
                    raise ConfigurationError(
                        "Testes exigem diretório independente dos dados reais."
                    )
            elif root != expected:
                raise ConfigurationError("O ambiente deve usar seu diretório próprio.")
        elif self.environment != "test":
            raise ConfigurationError("LOCALAPPDATA não está disponível.")
        object.__setattr__(self, "root", root)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "edisk.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "backups" / "pre-migration"

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.environ.get("EDISK_ENV", "development")
        if environment == "test":
            test_root = os.environ.get("EDISK_TEST_ROOT")
            if not test_root:
                raise ConfigurationError("EDISK_TEST_ROOT é obrigatório no ambiente de testes.")
            return cls("test", Path(test_root))
        if environment not in {"development", "production"}:
            raise ConfigurationError("EDISK_ENV inválido.")
        if "EDISK_TEST_ROOT" in os.environ:
            raise ConfigurationError("EDISK_TEST_ROOT só é permitido no ambiente de testes.")
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ConfigurationError("LOCALAPPDATA não está disponível.")
        root = _absolute(Path(local)) / "eDisk"
        if environment == "development":
            root /= "development"
        return cls(environment, root)
