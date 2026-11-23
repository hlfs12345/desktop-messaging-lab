"""Valida e monta instalador a partir de bundle existente, sem reconstruir runtimes."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import build_origin
from native_variant import require_variant
from release_metadata import ROOT, product

MANIFEST = "manifest-sha256.json"
ALLOWED_TOP = {
    "edisk.exe",
    "edisk-launcher.exe",
    "flutter_windows.dll",
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "THIRD_PARTY_NOTICES.md",
    "build-info.json",
    "content-report.json",
    "content-inventory.json",
    MANIFEST,
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON duplicada.")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_pairs)


def safe_relative(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./+\-]{1,240}", value):
        raise ValueError("Nome de arquivo inseguro.")
    for part in value.split("/"):
        if not part or part in (".", "..") or part.endswith("."):
            raise ValueError("Componente de caminho inseguro.")
        base = part.split(".")[0].upper()
        if base in {"CON", "AUX", "PRN", "NUL"} or re.fullmatch(r"(COM|LPT)[1-9]", base):
            raise ValueError("Nome reservado Windows.")
    return value


def safe_root(root):
    if not root.is_absolute() or str(root).startswith("\\\\"):
        raise ValueError("Raiz exige caminho local absoluto.")
    for item in (root, *root.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("Raiz com reparse point.")
    return root


def listing(root):
    safe_root(root)
    result = {}
    folded = set()
    for folder, directories, names in os.walk(root, followlinks=False):
        for name in [*directories, *names]:
            path = Path(folder) / name
            if path.is_symlink() or path.is_junction() or path.lstat().st_file_attributes & 0x400:
                raise ValueError("Reparse point nao permitido.")
        for name in names:
            path = Path(folder) / name
            relative = safe_relative(path.relative_to(root).as_posix())
            if relative.lower() in folded:
                raise ValueError("Colisao de nomes Windows.")
            folded.add(relative.lower())
            result[relative] = digest(path)
    return result


def check_manifest(root, expected):
    if not re.fullmatch("[0-9a-fA-F]{64}", expected):
        raise ValueError("Digest de manifesto invalido.")
    safe_root(root)
    # Conferir reparse points antes de abrir o manifesto.
    actual = listing(root)
    if actual.pop(MANIFEST, None) != expected.lower():
        raise ValueError("Digest esperado do manifesto diverge.")
    manifest = read_json(root / MANIFEST)
    if not isinstance(manifest, dict):
        raise ValueError("Manifesto invalido.")
    for name, value in manifest.items():
        safe_relative(name)
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("Hash invalido no manifesto.")
    if manifest != actual:
        raise ValueError("Manifesto diverge: arquivo ausente, extra ou alterado.")
    return actual


def validate(root, expected, channel, approval_id=None, approval_sha256=None):
    actual = check_manifest(root, expected)
    info = read_json(root / "build-info.json")
    value = product()
    native = value["version"] + "." + str(value["build"])
    if info.get("release_schema") != 1 or info.get("channel") != channel:
        raise ValueError("Bundle sem contrato de release ou canal incorreto.")
    if info.get("app_version") != native or info.get("architecture") != "x64":
        raise ValueError("Versao ou arquitetura divergente.")
    profile = "bundled/production" if channel == "candidato" else "bundled/test"
    if info.get("default_profile") != profile or info.get("installation_protocol") != 3:
        raise ValueError("Perfil ou protocolo de instalacao incorreto.")
    if not re.fullmatch("[0-9a-f]{40}", info.get("commit", "")):
        raise ValueError("Commit de origem invalido.")
    if type(info.get("worktree_dirty")) is not bool:
        raise ValueError("Procedencia Git ausente.")
    if channel == "candidato" and info["worktree_dirty"]:
        raise ValueError("Candidato exige Git limpo na origem.")
    if info.get("status") != "technical-checks-passed-clean-windows-pending":
        raise ValueError("Bundle sem checks tecnicos aprovados.")
    if info.get("commercial_approval") is not False:
        raise ValueError("Esta entrega nao aprova distribuicao comercial.")
    checks = info.get("checks", [])
    required = {"content", "packaged-backend", "packaged-windows"}
    if not required.issubset({x.get("name") for x in checks if x.get("exit_code") == 0}):
        raise ValueError("Checks obrigatorios ausentes.")
    inventory = read_json(root / "content-inventory.json")
    if inventory != {k: v for k, v in actual.items() if k != "content-inventory.json"}:
        raise ValueError("Inventario aprovado diverge do bundle.")
    for name in actual:
        top = name.split("/")[0]
        if top not in ALLOWED_TOP and top not in {"backend", "data", "LICENSES"}:
            raise ValueError("Arquivo inesperado no bundle: " + name)
    require_variant(root / "edisk.exe", channel, native)
    if channel == "candidato":
        build_origin.verify(root, approval_id, approval_sha256)
    import pefile

    pe = pefile.PE(str(root / "edisk.exe"))
    try:
        fields = [int(x) for x in native.split(".")]
        fixed = pe.VS_FIXEDFILEINFO[0]
        if (
            fixed.FileVersionMS != (fields[0] << 16 | fields[1])
            or fixed.FileVersionLS != (fields[2] << 16 | fields[3])
            or fixed.FileFlags & 1
        ):
            raise ValueError("Recurso de versao PE diverge ou runner e debug.")
    finally:
        pe.close()
    if not (root / "edisk-launcher.exe").is_file():
        raise ValueError("Launcher ausente.")
    spec = importlib.util.spec_from_file_location(
        "bundle_windows", ROOT / "scripts/bundle_windows.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    report = audit.validate(root)
    return info, actual, report


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError("Ferramenta falhou: " + result.stderr.decode(errors="replace")[-2000:])
    return result.stdout.decode("utf-8", errors="replace").strip()


def toolchain(iscc):
    lock = read_json(ROOT / "installer/toolchain.lock.json")
    if iscc.name.lower() != "iscc.exe":
        raise ValueError("Informe o ISCC.exe fixado, nao outro executavel.")
    for name, key in [
        ("ISCC.exe", "iscc_sha256"),
        ("ISCmplr.dll", "compiler_sha256"),
        ("license.txt", "license_sha256"),
    ]:
        if digest(iscc.parent / name) != lock[key]:
            raise ValueError("Toolchain difere da versao fixada: " + name)
    for name, expected in lock["compiler_files"].items():
        safe_relative(name)
        if digest(iscc.parent / name) != expected:
            raise ValueError("Componente do compilador diverge: " + name)
    if run([str(iscc), "--version"]) != lock["version"]:
        raise ValueError("Versao ISCC incorreta.")
    return lock


def quote(value):
    value = str(value)
    if any(x in value for x in ('"', "\r", "\n", "{", "}")):
        raise ValueError("Valor nao representavel com seguranca no Inno.")
    return value


def prepare(
    bundle,
    stage,
    manifest_digest,
    channel,
    test_instance=None,
    approval_id=None,
    approval_sha256=None,
):
    info, actual, audit = validate(bundle, manifest_digest, channel, approval_id, approval_sha256)
    if stage.exists():
        raise ValueError("Staging deve ser novo.")
    stage.mkdir(parents=True)
    payload = stage / "payload"
    shutil.copytree(bundle, payload)
    check_manifest(payload, manifest_digest)
    actual[MANIFEST] = manifest_digest.lower()
    value = product()
    identity = value["channels"][channel].copy()
    if test_instance:
        if channel != "tecnico" or not re.fullmatch("[0-9a-f]{32}", test_instance):
            raise ValueError("Instancia de ensaio invalida.")
        identity = {
            "app_id": "eDisk-Ensaio-" + test_instance,
            "directory": "eDisk-Ensaio-" + test_instance,
        }
    release = info["app_version"] + "-" + manifest_digest[:16].lower()
    approved = (
        "\n".join(
            [
                "EDISK-PAYLOAD-3",
                channel,
                info["default_profile"],
                info["app_version"],
                release,
                manifest_digest.lower(),
                actual["build-info.json"],
            ]
        )
        + "\n"
    )
    (payload / "approved-payload.txt").write_text(approved, encoding="ascii")
    actual["approved-payload.txt"] = digest(payload / "approved-payload.txt")
    inventory = "".join(f"{value}  {name}\n" for name, value in sorted(actual.items()))
    (payload / "inventory.txt").write_text(inventory, encoding="ascii")
    inventory_hash = digest(payload / "inventory.txt")
    config = {
        "AppId": identity["app_id"],
        "InstallFolder": identity["directory"],
        "AppVersion": info["app_version"],
        "Channel": channel,
        "ReleaseId": release,
        "InventoryHash": inventory_hash,
    }
    lines = [f'#define {key} "{quote(val)}"' for key, val in config.items()]
    if test_instance:
        lines.append("#define TestBuild 1")
    (stage / "config.iss").write_text("\n".join(lines) + "\n", encoding="utf-8")
    records = {**actual, "inventory.txt": inventory_hash}
    files = []
    checks = []
    for name, value_hash in sorted(records.items()):
        win = name.replace("/", "\\")
        parent = str(Path(win).parent)
        target = "{app}\\releases\\{#ReleaseId}"
        if parent != ".":
            target += "\\" + parent
        files.append(
            f'Source: "{quote(payload / name)}"; DestDir: "{target}"; Flags: ignoreversion'
        )
        checks.append(
            f"  if GetSHA256OfFile(ExpandConstant('{{app}}\\releases\\{{#ReleaseId}}\\{win}'))"
            f" <> '{value_hash}' then RaiseException('Integridade do payload invalida.');"
        )
    # Launcher estavel e aviso proprio do instalador, fora do payload versionado.
    files += [
        f'Source: "{quote(payload / "edisk-launcher.exe")}"; '
        'DestDir: "{app}"; Flags: ignoreversion',
        f'Source: "{quote(ROOT / "installer/Inno-LICENSE.txt")}"; '
        'DestDir: "{app}"; Flags: ignoreversion',
    ]
    (stage / "files.iss").write_text("\n".join(files) + "\n", encoding="utf-8")
    (stage / "checks.iss").write_text(
        "procedure VerifyPayload();\nbegin\n"
        + "\n".join(checks)
        + f"\n  if CountPayload(ExpandConstant('{{app}}\\releases\\{{#ReleaseId}}'))"
        f" <> {len(records)} then RaiseException('Arquivos extras no payload.');\nend;\n",
        encoding="utf-8",
    )
    return {
        **config,
        "bundle": info,
        "manifest_sha256": manifest_digest,
        "audit": audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--channel", choices=["tecnico", "candidato"], required=True)
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-instance")
    parser.add_argument("--approval-id")
    parser.add_argument("--approval-sha256")
    args = parser.parse_args()
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    work = ROOT / "build/installer" / identity
    work.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "failed-no-promotion",
        "windows_clean": "pendente",
        "signed": False,
        "commercial_approval": False,
        "id": identity,
    }
    try:
        report["recipe_sha256"] = build_origin.inputs()
        report["generator_tools"] = build_origin.host_tools()
        report["toolchain_lock_sha256"] = digest(ROOT / "installer/toolchain.lock.json")
        report["toolchain"] = toolchain(args.iscc.resolve())
        report["installer_commit"] = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        report["installer_worktree_dirty"] = bool(
            run(["git", "-C", str(ROOT), "status", "--porcelain"])
        )
        if args.channel == "candidato" and report["installer_worktree_dirty"]:
            raise ValueError("Candidato exige Git limpo no build do instalador.")
        stage = work / "stage"
        report["input"] = prepare(
            args.bundle.absolute(),
            stage,
            args.manifest_sha256,
            args.channel,
            args.test_instance,
            args.approval_id,
            args.approval_sha256,
        )
        report["bundle_identity"] = build_origin.bundle_identity(args.bundle.absolute())
        origin_id = report["input"]["bundle"]["origin_id"]
        record = build_origin.read_record("origin", origin_id)
        if record["artifact"] != report["bundle_identity"]:
            raise ValueError("Registro de build diverge do bundle tecnico.")
        report["build_origin"] = {
            "id": origin_id,
            "record_sha256": digest(build_origin.record_path("origin", origin_id)),
            "source_inputs": record["inputs"],
            "host_tools": record["host_tools"],
        }
        if args.channel == "candidato":
            report["provenance"] = build_origin.verify(
                args.bundle.absolute(), args.approval_id, args.approval_sha256
            )
        name = f"eDisk-{args.channel}-{report['input']['AppVersion']}-{identity}"
        run(
            [
                str(args.iscc),
                "--quiet",
                "--no-ide-signtools",
                "--define=Stage=" + str(stage),
                "--output-dir=" + str(work),
                "--output-filename=" + name,
                str(ROOT / "installer/edisk.iss"),
            ]
        )
        setup = work / (name + ".exe")
        if not setup.is_file() or setup.stat().st_size < 1024:
            raise ValueError("ISCC nao produziu instalador valido.")
        report["installer_sha256"] = digest(setup)
        report["installer_bytes"] = setup.stat().st_size
        report["status"] = "technical-build-passed-clean-windows-pending"
        output = args.output.absolute()
        safe_root(output.parent)
        if output.exists() or not output.is_relative_to(ROOT / "dist"):
            raise ValueError("Destino deve ser novo e ficar dentro de dist/.")
        promotion = work / "promote"
        promotion.mkdir()
        shutil.copyfile(setup, promotion / setup.name)
        (promotion / (setup.name + ".sha256")).write_text(
            report["installer_sha256"] + "  " + setup.name + "\n", encoding="ascii"
        )
        (promotion / "installer-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        if digest(promotion / setup.name) != report["installer_sha256"]:
            raise ValueError("Copia de promocao divergente.")
        if args.channel == "candidato":
            final_commit = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
            final_dirty = run(["git", "-C", str(ROOT), "status", "--porcelain"])
            if final_dirty or final_commit != report["installer_commit"]:
                raise ValueError("Git alterado durante build candidato; nao promover.")
        if report["recipe_sha256"] != build_origin.inputs():
            raise ValueError("Receita mudou durante compilacao do instalador.")
        promotion.rename(output)
        print(str(output / setup.name))
    except Exception as error:
        report["status"] = "failed-no-promotion"
        report["error"] = str(error)
        raise
    finally:
        (work / "installer-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
