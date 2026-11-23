"""Recursos invalidos sao rejeitados antes de executar o conteudo."""

import hashlib
import shutil
from pathlib import Path

import pytest

from app.persistencia.errors import ConfigurationError
from app.runtime.bundle_integrity import RESOURCE_FILES, verify_migrations


@pytest.fixture
def resources(tmp_path):
    original = Path(__file__).parents[1] / "src/app/persistencia/alembic"
    root = tmp_path / "recursos com espaços"
    for relative in RESOURCE_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / relative, target)
    expected = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in RESOURCE_FILES}
    return root, expected


def test_valid_resources_and_head(resources):
    verify_migrations(*resources)


@pytest.mark.parametrize("relative", RESOURCE_FILES)
@pytest.mark.parametrize("action", ["missing", "changed"])
def test_invalid_resources_fail_before_execution(resources, relative, action):
    root, expected = resources
    if action == "missing":
        (root / relative).unlink()
    else:
        (root / relative).write_text("raise AssertionError('nao executar')", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        verify_migrations(root, expected)


def test_extra_revision_fails(resources):
    root, expected = resources
    (root / "versions/extra.py").write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        verify_migrations(root, expected)


def test_wrong_head_even_with_matching_hash(resources):
    root, expected = resources
    revision = root / RESOURCE_FILES[-1]
    revision.write_text(
        revision.read_text(encoding="utf-8").replace(
            'revision = "0016_telegram_reativacao"', 'revision = "unexpected"'
        ),
        encoding="utf-8",
    )
    expected[RESOURCE_FILES[-1]] = hashlib.sha256(revision.read_bytes()).hexdigest()
    with pytest.raises(ConfigurationError):
        verify_migrations(root, expected)
