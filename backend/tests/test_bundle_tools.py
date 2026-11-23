"""Auditoria impede artefatos incompletos e adulterados antes da promocao."""

import ast
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from app.runtime.bundle_integrity import RESOURCE_FILES

spec = importlib.util.spec_from_file_location(
    "bundle_windows", Path(__file__).parents[2] / "scripts/bundle_windows.py"
)
bundle_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle_tools)


@pytest.mark.parametrize(
    "module",
    [
        "openpyxl.packaging",
        "openpyxl.packaging.manifest",
        "openpyxl.packaging.relationship",
    ],
)
def test_ooxml_packaging_is_runtime(module):
    assert not bundle_tools.forbidden_module(module)


@pytest.mark.parametrize(
    "module",
    [
        "packaging",
        "packaging.version",
        "setuptools._vendor.packaging",
        "app.packaging",
        "openpyxl.pytest",
        "openpyxl.packaging.pytest",
        "openpyxl.packaging.tests",
        "phonenumbers.tests",
    ],
)
def test_ooxml_namespace_does_not_allow_tools_or_tests(module):
    assert bundle_tools.forbidden_module(module)


def test_real_pyinstaller_recipe_declares_backend_resources():
    root = Path(__file__).parents[2]
    recipe = root / "backend/packaging/edisk-backend.spec"
    # Conferir a receita invocada pelo comando de empacotamento efetivo.
    packaging = (root / "scripts/empacotar-windows.ps1").read_text(encoding="utf-8")
    assert "'packaging/edisk-backend.spec'" in packaging
    tree = ast.parse(recipe.read_text(encoding="utf-8"), filename=str(recipe))
    assignments = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "required" for target in node.targets)
    ]
    assert len(assignments) == 1, "A receita deve declarar os recursos explicitamente."
    required = ast.literal_eval(assignments[0])
    assert isinstance(required, tuple)
    assert "versions/0005_contas_envio.py" in required
    assert "versions/0006_campanhas_rascunho.py" in required
    assert len(required) == len(set(required)), "Recursos duplicados na receita."
    assert set(required) == set(RESOURCE_FILES), {
        "ausentes": sorted(set(RESOURCE_FILES) - set(required)),
        "inesperados": sorted(set(required) - set(RESOURCE_FILES)),
    }
    # env.py e distribuido como recurso e seu import nao e descoberto pelo grafo do PYZ.
    analysis = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "Analysis"
    )
    hidden = next(ast.literal_eval(k.value) for k in analysis.keywords if k.arg == "hiddenimports")
    assert "app.campanhas.execucoes_models" in hidden


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "candidato"
    for name in bundle_tools.REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (root / "LICENSES/components.json").write_text("[]", encoding="utf-8")
    original = Path(__file__).parents[1] / "src/app/persistencia/alembic"
    for name in RESOURCE_FILES:
        target = root / "backend/_internal/app/persistencia/alembic" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / name, target)
    return root


@pytest.mark.parametrize(
    "name",
    ["edisk.exe", "backend/_internal/python314.dll", "msvcp140.dll", "LICENSES/Python-LICENSE.txt"],
)
def test_missing_required_prevents_validation(candidate, name):
    (candidate / name).unlink()
    with pytest.raises(ValueError, match="obrigatorio"):
        bundle_tools.validate(candidate)


@pytest.mark.parametrize(
    "name,content",
    [
        (".env", b"TOKEN=nao-distribuir"),
        ("dados.bin", b"SQLite format 3\0"),
        ("tests/exemplo.txt", b"fixture"),
        ("fonte.py", b"print('nao-distribuir')"),
    ],
)
def test_forbidden_files_prevent_validation(candidate, name, content):
    path = candidate / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with pytest.raises(ValueError):
        bundle_tools.validate(candidate)


def test_license_cannot_reference_outside_bundle(candidate):
    path = candidate / "LICENSES/components.json"
    path.write_text(json.dumps([{"texts": ["../externo.txt"]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="licenca"):
        bundle_tools.validate(candidate)


def test_manifest_rejects_changed_or_added_content(tmp_path, monkeypatch):
    (tmp_path / "payload.txt").write_text("original", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["bundle", "manifest", str(tmp_path)])
    bundle_tools.main()
    bundle_tools.main()
    (tmp_path / "payload.txt").write_text("adulterado", encoding="utf-8")
    with pytest.raises(ValueError, match="Manifesto divergente"):
        bundle_tools.main()

    (tmp_path / "payload.txt").write_text("original", encoding="utf-8")
    nested = tmp_path / "subdiretorio/manifest-sha256.json"
    nested.parent.mkdir()
    nested.write_text("tambem deve ser coberto", encoding="utf-8")
    with pytest.raises(ValueError, match="Manifesto divergente"):
        bundle_tools.main()
