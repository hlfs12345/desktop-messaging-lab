"""Entradas hostis e procedencia do instalador, sem executar setup."""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("installer_windows", SCRIPTS / "installer_windows.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
from release_metadata import build_profile, product  # noqa: E402


@pytest.fixture
def payload(tmp_path):
    (tmp_path / "arquivo.txt").write_text("original", encoding="ascii")
    manifest = {"arquivo.txt": installer.digest(tmp_path / "arquivo.txt")}
    (tmp_path / installer.MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    return tmp_path, installer.digest(tmp_path / installer.MANIFEST)


def test_valid_manifest_and_changed_content(payload):
    root, digest = payload
    assert installer.check_manifest(root, digest)
    (root / "arquivo.txt").write_text("alterado", encoding="ascii")
    with pytest.raises(ValueError, match="diverge"):
        installer.check_manifest(root, digest)


@pytest.mark.parametrize("mode", ["extra", "missing", "wrong-digest"])
def test_manifest_rejects_inconsistent_files(payload, mode):
    root, digest = payload
    if mode == "extra":
        (root / "extra.txt").touch()
    elif mode == "missing":
        (root / "arquivo.txt").unlink()
    else:
        digest = "0" * 64
    with pytest.raises(ValueError):
        installer.check_manifest(root, digest)


@pytest.mark.parametrize(
    "name",
    [
        "../externo",
        "/absoluto",
        "C:/fora",
        "a//b",
        "a\\b",
        "a./b",
        "a:b",
        "NUL.txt",
        "COM1.dat",
        "a/{app}",
        "a/..",
    ],
)
def test_unsafe_paths_rejected(name):
    with pytest.raises(ValueError):
        installer.safe_relative(name)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "duplicado.json"
    path.write_text('{"a": 1, "a": 2}', encoding="ascii")
    with pytest.raises(ValueError, match="duplicada"):
        installer.read_json(path)


def test_junction_rejected(payload, tmp_path):
    import _winapi

    root, digest = payload
    outside = tmp_path / "externo"
    outside.mkdir()
    junction = root / "atalho"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        with pytest.raises(ValueError, match="Reparse"):
            installer.check_manifest(root, digest)
    finally:
        junction.rmdir()


@pytest.mark.parametrize(
    "channel,dirty,changes,message",
    [
        ("candidato", True, {}, "limpo"),
        ("tecnico", False, {"channel": "candidato"}, "canal"),
        ("tecnico", False, {"architecture": "x86"}, "arquitetura"),
        ("tecnico", False, {"app_version": "9.9.9.9"}, "Versao"),
        ("tecnico", False, {"default_profile": "bundled/production"}, "Perfil"),
        ("tecnico", False, {"commit": "desconhecido"}, "Commit"),
        ("tecnico", False, {"status": "failed"}, "checks"),
    ],
)
def test_release_metadata_rejected(tmp_path, channel, dirty, changes, message):
    value = product()
    info = {
        "release_schema": 1,
        "installation_protocol": 3,
        "channel": channel,
        "app_version": value["version"] + "." + str(value["build"]),
        "architecture": "x64",
        "default_profile": "bundled/production" if channel == "candidato" else "bundled/test",
        "commit": "a" * 40,
        "worktree_dirty": dirty,
        "status": "technical-checks-passed-clean-windows-pending",
        "commercial_approval": False,
    }
    info.update(changes)
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="ascii")
    manifest = {"build-info.json": installer.digest(tmp_path / "build-info.json")}
    (tmp_path / installer.MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(ValueError, match=message):
        installer.validate(tmp_path, installer.digest(tmp_path / installer.MANIFEST), channel)


@pytest.mark.parametrize(
    "definitions,valid",
    [
        ({}, (False, "tecnico")),
        ({"EDISK_SOURCE_BACKEND": "true"}, (True, "tecnico")),
        ({"EDISK_DISTRIBUTION": "true"}, (False, "candidato")),
        ({"EDISK_DISTRIBUTION": "true", "EDISK_SOURCE_BACKEND": "true"}, None),
        ({"EDISK_DISTRIBUTION": "yes"}, None),
    ],
)
def test_native_build_profiles(tmp_path, definitions, valid):
    config = tmp_path / "generated.cmake"
    raw = ",".join(
        base64.b64encode((k + "=" + v).encode()).decode() for k, v in definitions.items()
    )
    if not raw:
        raw = base64.b64encode(b"FLUTTER_VERSION=teste").decode()
    config.write_text('"DART_DEFINES=' + raw + '"', encoding="ascii")
    if valid is None:
        with pytest.raises(ValueError):
            build_profile(config)
    else:
        assert build_profile(config) == valid


def test_untrusted_compiler_is_never_executed(tmp_path, monkeypatch):
    executable = tmp_path / "ISCC.exe"
    executable.write_bytes(b"compilador nao aprovado")

    def unexpected_execution(_command):
        raise AssertionError("Compilador divergente foi executado antes de validar hash")

    monkeypatch.setattr(installer, "run", unexpected_execution)
    with pytest.raises(ValueError, match="Toolchain"):
        installer.toolchain(executable)


def test_bundle_without_release_contract_is_rejected(payload):
    root, _digest = payload
    (root / "build-info.json").write_text('{"worktree_dirty": true}', encoding="ascii")
    manifest = installer.listing(root)
    manifest.pop(installer.MANIFEST)
    (root / installer.MANIFEST).write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(ValueError, match="contrato"):
        installer.validate(root, installer.digest(root / installer.MANIFEST), "tecnico")


def test_installed_backend_uppercase_layout_requires_coordinator(tmp_path, monkeypatch):
    from app.runtime.installed_coordination import verify_installed_coordination

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "RELEASES/v/backend/edisk-backend.exe"))
    monkeypatch.delenv("EDISK_INSTALL_JOB", raising=False)
    with pytest.raises(ValueError, match="coordenador"):
        verify_installed_coordination()
