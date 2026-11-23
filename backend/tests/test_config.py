"""Isolamento de ambientes e caminhos absolutos."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.persistencia.errors import ConfigurationError
from app.persistencia.lifecycle import Persistence


def test_environments_have_separate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "perfil"))
    monkeypatch.delenv("EDISK_TEST_ROOT")
    monkeypatch.delenv("EDISK_ENV")
    development = Settings.from_environment()
    monkeypatch.setenv("EDISK_ENV", "production")
    production = Settings.from_environment()
    testing = Settings("test", tmp_path / "testes")
    assert development.database_path == tmp_path / "perfil/eDisk/development/data/edisk.sqlite3"
    assert production.database_path == tmp_path / "perfil/eDisk/data/edisk.sqlite3"
    assert len({item.database_path for item in (development, production, testing)}) == 3
    assert len({item.logs_dir for item in (development, production, testing)}) == 3
    assert len({item.snapshots_dir for item in (development, production, testing)}) == 3
    instances = [Persistence(item) for item in (production, development, testing)]
    try:
        for instance in instances:
            instance.start()
            assert instance.probe()
    finally:
        for instance in instances:
            instance.close()


@pytest.mark.parametrize("environment", ["production", "development"])
def test_real_environment_requires_localappdata(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("EDISK_ENV", environment)
    monkeypatch.delenv("EDISK_TEST_ROOT")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(ConfigurationError):
        Settings.from_environment()


@pytest.mark.parametrize("environment", ["prod", "", "invalid"])
def test_invalid_environment_fails(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("EDISK_ENV", environment)
    with pytest.raises(ConfigurationError):
        Settings.from_environment()


def test_tests_require_explicit_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDISK_TEST_ROOT")
    with pytest.raises(ConfigurationError):
        Settings.from_environment()


def test_test_override_is_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDISK_ENV", "production")
    with pytest.raises(ConfigurationError):
        Settings.from_environment()


def test_relative_and_source_paths_are_rejected() -> None:
    for path in (Path("relative"), Path(__file__).resolve().parent):
        with pytest.raises(ConfigurationError):
            Settings("test", path)


def test_test_root_cannot_overlap_real_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "perfil"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for path in (local / "eDisk", local / "eDisk/development", local):
        with pytest.raises(ConfigurationError):
            Settings("test", path)


def test_changing_working_directory_does_not_change_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings.from_environment()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert Settings.from_environment() == settings
    persistence = Persistence(settings)
    persistence.start()
    persistence.close()
    assert settings.database_path.exists()
    assert list(elsewhere.iterdir()) == []
