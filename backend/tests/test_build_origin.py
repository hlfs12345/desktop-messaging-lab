"""DPAPI real e adulteracao da referencia independente, em cofre temporario de teste."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import build_origin as origin


@pytest.fixture
def approved(tmp_path, monkeypatch):
    monkeypatch.setattr(origin, "vault", lambda: tmp_path)
    monkeypatch.setattr(origin, "host_tools", lambda: {"compiler": "fixture-reviewed"})
    state = {"commit": "a" * 40, "worktree_dirty": False}
    recipe = {
        "installer/toolchain.lock.json": origin.sha(origin.ROOT / "installer/toolchain.lock.json")
    }
    artifact = {
        "commit": "a" * 40,
        "worktree_dirty": False,
        "channel": "candidato",
        "profile": "bundled/production",
        "version": "0.1.0.2",
        "bundle_digest": "b" * 64,
        "manifest_sha256": "c" * 64,
    }
    monkeypatch.setattr(origin, "git_state", lambda: state.copy())
    monkeypatch.setattr(origin, "inputs", lambda: recipe.copy())
    current = copy.deepcopy(artifact)
    monkeypatch.setattr(origin, "bundle_identity", lambda _: current.copy())
    identity = "e" * 32
    record = {
        "schema": 1,
        "host_tools": origin.host_tools(),
        "id": identity,
        "git": state,
        "inputs": recipe,
        "channel": "candidato",
        "artifact": artifact,
        "toolchain_lock_sha256": origin.sha(origin.ROOT / "installer/toolchain.lock.json"),
    }
    digest = origin.write_record("origin", identity, record)
    approval = origin.approve(identity, digest)
    return identity, approval, current, recipe


def test_dpapi_roundtrip_and_tampering():
    sealed = origin.crypt(b"build protegido")
    assert origin.crypt(sealed, True) == b"build protegido"
    bad = bytearray(sealed)
    bad[-1] ^= 1
    with pytest.raises(ValueError):
        origin.crypt(bytes(bad), True)


def test_approved_exact_artifact(approved):
    identity, digest, _, _ = approved
    result = origin.verify(Path("."), identity, digest)
    assert result["origin_id"] == identity
    assert result["bundle_digest"] == "b" * 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit", "f" * 40),
        ("bundle_digest", "f" * 64),
        ("manifest_sha256", "f" * 64),
        ("channel", "tecnico"),
        ("profile", "bundled/test"),
        ("version", "0.1.0.3"),
        ("worktree_dirty", True),
    ],
)
def test_recomputed_manifest_and_inconsistent_metadata_rejected(approved, field, value):
    identity, digest, current, _ = approved
    current[field] = value
    with pytest.raises(ValueError, match="diverge"):
        origin.verify(Path("."), identity, digest)


def test_missing_and_divergent_approval(approved):
    identity, digest, _, _ = approved
    for identity_value, digest_value in ((None, None), ("0" * 32, digest), (identity, "0" * 64)):
        with pytest.raises(ValueError):
            origin.verify(Path("."), identity_value, digest_value)


def test_recipe_hashes_are_checked(approved):
    identity, digest, _, recipe = approved
    recipe["scripts/receita.py"] = "0" * 64
    with pytest.raises(ValueError, match="diverge"):
        origin.verify(Path("."), identity, digest)


def test_consumer_cannot_use_edited_plaintext_origin(approved):
    identity, digest, _, _ = approved
    path = origin.record_path("origin", identity)
    path.write_bytes(b'{"worktree_dirty":false}')
    with pytest.raises(ValueError):
        origin.verify(Path("."), identity, digest)


@pytest.mark.parametrize("field,value", [("commit", "f" * 40), ("app_version", "0.1.0.3")])
def test_recalculated_physical_manifest_does_not_authenticate(tmp_path, monkeypatch, field, value):
    import json
    import shutil

    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/tests"))
    import installer_windows as installer
    from pe_fixture import variant

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(origin, "vault", lambda: tmp_path / "trust")
    monkeypatch.setattr(origin, "inputs", lambda: {"recipe": "reviewed"})
    monkeypatch.setattr(origin, "host_tools", lambda: {"compiler": "fixture"})
    monkeypatch.setattr(origin, "git_state", lambda: {"commit": "a" * 40, "worktree_dirty": False})
    shutil.copyfile(
        origin.ROOT / "build/installer-native/Release/fixture_runner.exe", bundle / "edisk.exe"
    )
    variant(bundle / "edisk.exe", "0.1.0.2", channel=2, profile=2)
    info = {
        "commit": "a" * 40,
        "worktree_dirty": False,
        "channel": "candidato",
        "default_profile": "bundled/production",
        "app_version": "0.1.0.2",
    }

    def seal():
        (bundle / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
        values = installer.listing(bundle)
        values.pop(installer.MANIFEST, None)
        (bundle / installer.MANIFEST).write_text(json.dumps(values), encoding="utf-8")

    seal()
    identity = "9" * 32
    record = {
        "schema": 1,
        "id": identity,
        "channel": "candidato",
        "git": origin.git_state(),
        "inputs": origin.inputs(),
        "host_tools": origin.host_tools(),
        "artifact": origin.bundle_identity(bundle),
        "toolchain_lock_sha256": origin.sha(origin.ROOT / "installer/toolchain.lock.json"),
    }
    ref = origin.write_record("origin", identity, record)
    approval = origin.approve(identity, ref)
    assert (
        origin.verify(bundle, identity, approval)["bundle_digest"]
        == record["artifact"]["bundle_digest"]
    )
    info[field] = value
    if field == "app_version":
        variant(bundle / "edisk.exe", value, channel=2, profile=2)
    seal()
    assert installer.check_manifest(bundle, origin.sha(bundle / installer.MANIFEST))
    with pytest.raises(ValueError, match="diverge"):
        origin.verify(bundle, identity, approval)


def test_text_marker_cannot_override_structured_pe_variant(tmp_path):
    import shutil

    sys.path.insert(0, str(Path(__file__).parents[2] / "scripts/tests"))
    from native_variant import require_variant
    from pe_fixture import variant

    executable = tmp_path / "runner.exe"
    shutil.copyfile(origin.ROOT / "build/installer-native/Release/fixture_runner.exe", executable)
    variant(executable, "0.1.0.2")
    with executable.open("ab") as stream:
        stream.write(b"EDISK_RUNNER_POLICY_V1:candidato:0.1.0.2")
    assert require_variant(executable, "tecnico", "0.1.0.2")["profile"] == "bundled/test"
    with pytest.raises(ValueError):
        require_variant(executable, "candidato", "0.1.0.2")
