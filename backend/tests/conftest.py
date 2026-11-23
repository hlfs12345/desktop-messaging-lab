"""Toda execução de testes, inclusive subprocessos, usa dados descartáveis."""

import secrets
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.instance_auth import InstanceAuthorization


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDISK_ENV", "test")
    monkeypatch.setenv("EDISK_TEST_ROOT", str(tmp_path / "application"))
    # Login não pode provocar rede real em testes que apenas cadastram credenciais.
    from app.contatos.errors import NegocioError

    class TelegramSemRede:
        def verificar(self, *args, **kwargs):
            raise NegocioError("telegram_comunicacao", "Transporte de teste indisponível.")

        def cancelar(self):
            pass

    monkeypatch.setattr("app.runtime.telegram_recepcao.Telegram", TelegramSemRede)


@pytest.fixture
def settings() -> Settings:
    return Settings.from_environment()


@pytest.fixture
def authorization() -> InstanceAuthorization:
    return InstanceAuthorization(secrets.token_bytes(16), secrets.token_bytes(32))
