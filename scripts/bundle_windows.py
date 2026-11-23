"""Montagem e auditoria local; nao instala ferramentas nem acessa a rede."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pefile
from packaging.requirements import Requirement
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "edisk.exe",
    "flutter_windows.dll",
    "data/app.so",
    "data/icudtl.dat",
    "data/flutter_assets/NOTICES.Z",
    "backend/edisk-backend.exe",
    "backend/_internal/python314.dll",
    "LICENSES/components.json",
    "THIRD_PARTY_NOTICES.md",
    "build-info.json",
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "LICENSES/Python-LICENSE.txt",
    "LICENSES/Flutter-LICENSE.txt",
    "LICENSES/Engine-LICENSE.txt",
    "LICENSES/PyInstaller-COPYING.txt",
    "LICENSES/native/sources.json",
]
FORBIDDEN = {
    "tests",
    "test",
    "__pycache__",
    ".venv",
    ".git",
    ".dart_tool",
    "backups",
    "logs",
    "credentials",
    "sessions",
    "node_modules",
}
TOOLS = {
    "pytest",
    "ruff",
    "setuptools",
    "pip",
    "uv",
    "httpx2",
    "httpcore2",
    "pygments",
    "packaging",
    "pluggy",
    "iniconfig",
    "colorama",
}
SYSTEM_DLLS = set(
    """advapi32 avrt bcrypt bcryptprimitives cabinet cfgmgr32 combase
comctl32 comdlg32 crypt32 cryptbase cryptsp d2d1 d3d9 d3d11 d3d12 dcomp dbghelp
dhcpcsvc dnsapi dsound dwmapi dwrite dxgi dxva2 gdi32 hid imm32 iphlpapi kernel32
kernelbase mf mfplat mfreadwrite mfuuid mmdevapi mpr msimg32 msvcrt mswsock
ncrypt netapi32 normaliz ntdll ole32 oleacc oleaut32 opengl32 powrprof propsys
psapi rpcrt4 secur32 setupapi shcore shell32 shlwapi sspicli ucrtbase urlmon
uiautomationcore user32 userenv usp10 uxtheme version win32u windowscodecs winhttp wininet winmm
winnsi winspool winscard wintrust wldap32 ws2_32 wtsapi32""".split()
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def runtime_packages():
    pending = [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "argon2-cffi",
        "phonenumbers",
        "openpyxl",
        "defusedxml",
    ]
    found = {}
    while pending:
        dist = metadata.distribution(pending.pop())
        name = dist.metadata["Name"].lower().replace("_", "-")
        if name in found:
            continue
        found[name] = dist
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return found


def stage(bundle, flutter, backend, crt, sdk, vs):
    if bundle.exists():
        raise ValueError("Staging deve ser novo.")
    bundle.mkdir(parents=True)
    for item in flutter.iterdir():
        if item.name == "data":
            shutil.copytree(item, bundle / item.name)
        elif item.suffix.lower() in {".exe", ".dll"}:
            shutil.copyfile(item, bundle / item.name)
    shutil.copytree(backend, bundle / "backend")
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        origin = crt / name
        if "redist" not in str(origin).lower() or "debug_nonredist" in str(origin).lower():
            raise ValueError("Origem CRT deve ser Redist release do toolchain.")
        shutil.copyfile(origin, bundle / name)
        if (bundle / "backend/_internal" / name).exists():
            shutil.copyfile(origin, bundle / "backend/_internal" / name)
    licenses = bundle / "LICENSES"
    licenses.mkdir()
    components = []
    for name, dist in sorted(runtime_packages().items()):
        texts = []
        for item in dist.files or []:
            if any(
                word in str(item).lower() for word in ("license", "licence", "notice", "copying")
            ):
                origin = Path(dist.locate_file(item))
                if origin.is_file():
                    target = licenses / name / Path(item).name
                    target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(origin, target)
                    texts.append(target.relative_to(bundle).as_posix())
        if not texts:
            raise ValueError("Licenca ausente: " + name)
        components.append(
            {
                "name": name,
                "version": dist.version,
                "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                "texts": texts,
            }
        )
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    shutil.copyfile(sdk / "LICENSE", licenses / "Flutter-LICENSE.txt")
    shutil.copyfile(sdk / "bin/cache/pkg/sky_engine/LICENSE", licenses / "Engine-LICENSE.txt")
    legal = ROOT / "backend/packaging/licenses"
    for name, record in json.loads((legal / "sources.json").read_text(encoding="utf-8")).items():
        if digest(legal / name) != record["file_sha256"]:
            raise ValueError("Texto de licenca alterado: " + name)
    shutil.copytree(legal, licenses / "native")
    redist_texts = sorted((vs / "Licenses").rglob("Redist.txt"))
    if not redist_texts:
        raise ValueError("Aviso de origem Microsoft ausente do toolchain.")
    shutil.copyfile(redist_texts[0], licenses / "Microsoft-Redist-origin.txt")
    bootloader = metadata.distribution("pyinstaller")
    shutil.copyfile(
        bootloader.locate_file("pyinstaller-6.22.3.dist-info/licenses/COPYING.txt"),
        licenses / "PyInstaller-COPYING.txt",
    )
    write_json(licenses / "components.json", components)
    shutil.copyfile(
        ROOT / "backend/packaging/THIRD_PARTY_NOTICES.md", bundle / "THIRD_PARTY_NOTICES.md"
    )


def forbidden_module(name):
    parts = name.lower().split(".")
    # OOXML usa este namespace próprio para manifestos/relacionamentos ZIP.
    # Não é a ferramenta Python de distribuição, nem libera outros descendentes.
    if parts[:2] == ["openpyxl", "packaging"]:
        parts = [parts[0], *parts[2:]]
    return any(part in {"test", "tests"} or part in TOOLS for part in parts)


def validate(bundle):
    for name in REQUIRED:
        if not (bundle / name).is_file():
            raise ValueError("Arquivo obrigatorio ausente: " + name)
    components = json.loads((bundle / "LICENSES/components.json").read_text(encoding="utf-8"))
    for component in components:
        if not component["texts"]:
            raise ValueError("Componente sem texto de licenca.")
        for text in component["texts"]:
            target = (bundle / text).resolve()
            if not target.is_relative_to(bundle.resolve()) or not target.is_file():
                raise ValueError("Texto de licenca ausente ou fora do bundle.")
    from app.runtime.bundle_integrity import RESOURCE_FILES, verify_migrations

    source_resources = ROOT / "backend/src/app/persistencia/alembic"
    expected = {name: digest(source_resources / name) for name in RESOURCE_FILES}
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        verify_migrations(bundle / "backend/_internal/app/persistencia/alembic", expected)
    finally:
        sys.dont_write_bytecode = previous_bytecode
    entries = list(bundle.rglob("*"))
    if any(p.is_symlink() for p in entries):
        raise ValueError("Links nao permitidos no bundle.")
    files = [p for p in entries if p.is_file()]
    for path in files:
        relative = path.relative_to(bundle)
        if path.is_symlink() or any(x.lower() in FORBIDDEN for x in relative.parts):
            raise ValueError("Conteudo proibido: " + relative.as_posix())
        if path.suffix.lower() in {
            ".sqlite3",
            ".sqlite",
            ".db",
            ".log",
            ".pdb",
            ".key",
            ".pem",
            ".pfx",
            ".bak",
        } or path.name.startswith(".env"):
            raise ValueError("Arquivo proibido: " + relative.as_posix())
        if path.suffix == ".py" and not relative.as_posix().startswith(
            "backend/_internal/app/persistencia/alembic/"
        ):
            raise ValueError("Fonte inesperada: " + relative.as_posix())
        with path.open("rb") as stream:
            prefix = stream.read(32)
        if prefix.startswith(b"SQLite format 3"):
            raise ValueError("Banco encontrado no bundle.")
    archive = CArchiveReader(str(bundle / "backend/edisk-backend.exe"))
    modules = set()
    for name, entry in archive.toc.items():
        if entry[-1] == "z":
            modules.update(archive.open_embedded_archive(name).toc)
    if "app.runtime._bundle_catalog" not in modules:
        raise ValueError("Catalogo de migracoes ausente do executavel.")
    for name in modules:
        if forbidden_module(name):
            raise ValueError("Modulo indevido no executavel: " + name)
    with zipfile.ZipFile(bundle / "backend/_internal/base_library.zip") as archive:
        for name in archive.namelist():
            if any(part in FORBIDDEN for part in name.split("/")):
                raise ValueError("Teste na biblioteca padrao.")
    # Cada PE e cada import/delay-import: nenhum runtime do desenvolvedor e aceito.
    imports = {}
    for path in files:
        if path.suffix.lower() not in {".exe", ".dll", ".pyd", ".so"}:
            continue
        if path.suffix == ".so":
            # Flutter Windows AOT e um snapshot ELF consumido pelo engine, nao DLL PE.
            header = path.read_bytes()[:20]
            if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
                raise ValueError("Snapshot AOT nao e ELF x64.")
            continue
        pe = pefile.PE(str(path), fast_load=True)
        if pe.FILE_HEADER.Machine != 0x8664:
            raise ValueError("Binario fora de Windows x64: " + path.name)
        pe.parse_data_directories(directories=[1, 13])
        dependencies = sorted(
            {
                entry.dll.decode("ascii").lower()
                for directory in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
                for entry in getattr(pe, directory, [])
            }
        )
        pe.close()
        search = [path.parent, bundle]
        if path.is_relative_to(bundle / "backend"):
            search = [path.parent, bundle / "backend", bundle / "backend/_internal"]
        dlls = {p.name.lower() for folder in search for p in folder.glob("*.dll")}
        for name in dependencies:
            if (
                name not in dlls
                and not name.startswith(("api-ms-", "ext-ms-"))
                and Path(name).stem not in SYSTEM_DLLS
            ):
                raise ValueError("DLL sem resolucao autorizada: " + name)
        imports[path.relative_to(bundle).as_posix()] = dependencies
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "python_modules": len(modules),
        "pe_imports": imports,
    }


def manifest(bundle):
    return {
        p.relative_to(bundle).as_posix(): digest(p)
        for p in sorted(bundle.rglob("*"))
        if p.is_file() and p != bundle / "manifest-sha256.json"
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "stage", "validate", "manifest"])
    parser.add_argument("bundle", type=Path, nargs="?", default=ROOT)
    for option in ("flutter", "backend", "crt", "sdk", "vs"):
        parser.add_argument("--" + option, type=Path)
    args = parser.parse_args()
    if args.action == "preflight":
        import struct

        assert sys.version_info[:3] == (3, 14, 10), "Python 3.14.10 obrigatorio."
        assert struct.calcsize("P") == 8, "Python x64 obrigatorio."
        assert metadata.version("pyinstaller") == "6.22.3"
        assert metadata.version("pyinstaller-hooks-contrib") == "2026.9"
        print("Toolchain Python validado.")
    elif args.action == "stage":
        stage(args.bundle, args.flutter, args.backend, args.crt, args.sdk, args.vs)
    elif args.action == "validate":
        report = validate(args.bundle)
        # A conferencia posterior nao altera um bundle ja manifestado/RX.
        if not (args.bundle / "content-report.json").exists():
            write_json(args.bundle / "content-report.json", report)
        print("Conteudo valido:", report["files"], "arquivos;", report["bytes"], "bytes")
    else:
        expected = manifest(args.bundle)
        target = args.bundle / "manifest-sha256.json"
        if target.exists():
            if json.loads(target.read_text(encoding="utf-8")) != expected:
                raise ValueError("Manifesto divergente.")
            print("Manifesto conferido sem alterar o bundle.")
        else:
            write_json(target, expected)


if __name__ == "__main__":
    main()
