"""Registro local DPAPI. Aprovacao humana separada; nao e atestado de terceiro."""

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from native_variant import require_variant
from release_metadata import ROOT


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crypt(data, decrypt=False):
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    if decrypt:
        ok = library.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = library.CryptProtectData(
            ctypes.byref(source),
            "eDisk build origin v1",
            None,
            None,
            None,
            1,
            ctypes.byref(target),
        )
    if not ok:
        raise ValueError("Registro DPAPI ausente, adulterado ou de outro usuario.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(target.data)


def vault():
    path = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, path) != 0:
        raise ValueError("LocalAppData indisponivel.")
    result = Path(path.value) / "eDisk-build-trust/v1"
    for item in (result, *result.parents):
        if item.exists() and (item.is_symlink() or item.is_junction()):
            raise ValueError("Reparse point no registro confiavel.")
    return result


def record_path(kind, identity):
    if not re.fullmatch("[0-9a-f]{32}", identity):
        raise ValueError("Referencia de origem invalida.")
    return vault() / (kind + "-" + identity + ".dpapi")


def write_record(kind, identity, value):
    path = record_path(kind, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(crypt(canonical(value)))
        stream.flush()
        os.fsync(stream.fileno())
    return sha(path)


def read_record(kind, identity, expected=None):
    path = record_path(kind, identity)
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("Registro confiavel ausente.")
    if expected and sha(path) != expected:
        raise ValueError("Digest independente do registro diverge.")
    return json.loads(crypt(path.read_bytes(), True))


def git_state():
    def git(*args):
        return subprocess.check_output(["git", "-C", str(ROOT), *args], encoding="utf-8").strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "worktree_dirty": bool(git("status", "--porcelain")),
    }


def inputs():
    names = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ]
        )
        .decode()
        .split("\0")
    )
    prefixes = (
        "scripts/",
        "installer/",
        "backend/src/",
        "backend/tests/",
        "frontend/test/",
        "backend/packaging/",
        "frontend/lib/",
        "frontend/windows/",
    )
    exact = {
        "backend/uv.lock",
        "backend/pyproject.toml",
        "frontend/pubspec.yaml",
        "frontend/pubspec.lock",
    }
    selected = {name for name in names if name and (name.startswith(prefixes) or name in exact)}
    return {name: sha(ROOT / name) for name in sorted(selected) if (ROOT / name).is_file()}


def bundle_identity(bundle):
    from installer_windows import MANIFEST, check_manifest, read_json

    manifest = sha(bundle / MANIFEST)
    files = check_manifest(bundle, manifest)
    files[MANIFEST] = manifest
    info = read_json(bundle / "build-info.json")
    variant = require_variant(bundle / "edisk.exe", info["channel"], info["app_version"])
    return {
        "bundle_digest": hashlib.sha256(canonical(files)).hexdigest(),
        "manifest_sha256": manifest,
        "files": files,
        "variant": variant,
        "commit": info["commit"],
        "worktree_dirty": info["worktree_dirty"],
        "channel": info["channel"],
        "profile": info["default_profile"],
        "version": info["app_version"],
    }


def host_tools():
    vswhere = (
        Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    vs = Path(
        subprocess.check_output(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            encoding="utf-8",
        ).strip()
    )
    version = (
        (vs / "VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    flutter = Path(shutil.which("flutter"))
    sdk = flutter.parent.parent
    paths = {
        "python": Path(sys.executable),
        "uv": Path(shutil.which("uv")),
        "flutter-command": flutter,
        "dart": sdk / "bin/cache/dart-sdk/bin/dart.exe",
        "flutter-tools": sdk / "bin/cache/flutter_tools.snapshot",
        "cl": vs / ("VC/Tools/MSVC/" + version + "/bin/Hostx64/x64/cl.exe"),
        "cmake": vs / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe",
    }
    return {
        "msvc_version": version,
        "binaries": {key: {"path": str(path), "sha256": sha(path)} for key, path in paths.items()},
    }


def begin(channel):
    identity = uuid.uuid4().hex
    state = git_state()
    if channel == "candidato" and state["worktree_dirty"]:
        raise ValueError("Candidato exige fonte Git limpa.")
    write_record(
        "pending",
        identity,
        {
            "schema": 1,
            "id": identity,
            "git": state,
            "channel": channel,
            "inputs": inputs(),
            "host_tools": host_tools(),
        },
    )
    return identity


def finish(identity, bundle):
    pending = read_record("pending", identity)
    if (
        pending["git"] != git_state()
        or pending["inputs"] != inputs()
        or pending["host_tools"] != host_tools()
    ):
        raise ValueError("Fontes ou receita mudaram durante o build.")
    artifact = bundle_identity(bundle)
    info = json.loads((bundle / "build-info.json").read_text(encoding="utf-8-sig"))
    if (
        artifact["commit"] != pending["git"]["commit"]
        or artifact["worktree_dirty"] != pending["git"]["worktree_dirty"]
        or artifact["channel"] != pending["channel"]
        or info.get("origin_id") != identity
        or info.get("status") != "technical-checks-passed-clean-windows-pending"
        or any(check.get("exit_code") != 0 for check in info["checks"])
    ):
        raise ValueError("Resultado do build contradiz a origem iniciada.")
    value = {
        **pending,
        "artifact": artifact,
        "checks": info["checks"],
        "tools": {
            key: info.get(key)
            for key in (
                "python",
                "pyinstaller",
                "hooks",
                "flutter",
                "engine",
                "crt",
                "uv_lock_sha256",
                "pub_lock_sha256",
            )
        },
        "python_exe_sha256": sha(Path(sys.executable)),
        "toolchain_lock_sha256": sha(ROOT / "installer/toolchain.lock.json"),
    }
    return write_record("origin", identity, value)


def approve(identity, expected):
    if not expected or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("Aprovacao exige digest independente do registro de origem.")
    value = read_record("origin", identity, expected)
    if (
        value["channel"] != "candidato"
        or value["git"]["worktree_dirty"]
        or value["git"] != git_state()
        or value["inputs"] != inputs()
        or value["host_tools"] != host_tools()
    ):
        raise ValueError("Somente origem candidata limpa e revisada pode ser aprovada.")
    return write_record(
        "approval",
        identity,
        {
            "schema": 1,
            "origin_id": identity,
            "origin_record_sha256": expected,
            "artifact": value["artifact"],
            "approval": "owner-reviewed-local-build",
        },
    )


def verify(bundle, identity, expected):
    if not identity or not expected:
        raise ValueError("Candidato exige referencia independente da aprovacao.")
    approval = read_record("approval", identity, expected)
    origin = read_record("origin", identity, approval["origin_record_sha256"])
    artifact = bundle_identity(bundle)
    if (
        approval.get("schema") != 1
        or approval.get("origin_id") != identity
        or origin.get("id") != identity
        or origin.get("channel") != "candidato"
        or origin["git"]["worktree_dirty"]
        or artifact["worktree_dirty"]
        or origin["inputs"] != inputs()
        or origin["host_tools"] != host_tools()
        or origin["git"] != git_state()
        or origin["artifact"] != artifact
        or approval["artifact"] != artifact
        or origin["toolchain_lock_sha256"] != sha(ROOT / "installer/toolchain.lock.json")
    ):
        raise ValueError("Procedencia ou aprovacao diverge do bundle/fonte/receita.")
    return {
        "origin_id": identity,
        "approval_sha256": expected,
        "origin_record_sha256": approval["origin_record_sha256"],
        "bundle_digest": artifact["bundle_digest"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["begin", "finish", "inspect", "approve"])
    p.add_argument("--channel", choices=["tecnico", "candidato"])
    p.add_argument("--origin-id")
    p.add_argument("--origin-sha256")
    p.add_argument("--bundle", type=Path)
    a = p.parse_args()
    if a.action == "begin":
        result = begin(a.channel)
    elif a.action == "finish":
        result = finish(a.origin_id, a.bundle)
    elif a.action == "inspect":
        result = read_record("origin", a.origin_id, a.origin_sha256)
    else:
        result = approve(a.origin_id, a.origin_sha256)
    print(result if isinstance(result, str) else json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
