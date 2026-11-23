"""Primeiro marco real Inno; somente instalacao e arquivos descartaveis."""

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import uuid
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(command, log=None):
    from installer_attempt import run_attempt

    executor = run_attempt if "/VERYSILENT" in command else subprocess.run
    result = executor(
        command,
        capture_output=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if "/VERYSILENT" in command:
        requested = next((Path(value[5:]) for value in command if value.startswith("/LOG=")), None)
        if requested:
            with requested.open("xb") as target:
                target.write(Path(result.edisk_log).read_bytes())
    if log:
        log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Comando falhou ({result.returncode}); consultar {log}.")
    return result.stdout.decode("utf-8", errors="replace").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, required=True)
    args = parser.parse_args()
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Execute o ensaio com token Windows nao elevado.")
    version = run([str(args.iscc), "--version"])
    if version != "7.1.1":
        raise RuntimeError("Inno Setup 7.1.1 obrigatorio: " + version)
    identity = uuid.uuid4().hex
    work = ROOT / "build" / "installer-prototype" / identity
    work.mkdir(parents=True, exist_ok=False)
    install = Path(os.environ["LOCALAPPDATA"]) / "Programs" / ("eDisk-Prototipo-" + identity)
    group = (
        Path(os.environ["APPDATA"])
        / "Microsoft/Windows/Start Menu/Programs"
        / ("eDisk Prototipo " + identity)
    )
    registry = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\eDisk-Prototipo-" + identity + "_is1"
    )
    if install.exists():
        raise RuntimeError("Destino de ensaio ja existe.")
    report = {
        "status": "running",
        "iscc_version": version,
        "install": str(install),
        "app_id": "eDisk-Prototipo-" + identity,
        "elevated": False,
        "iscc_sha256": hashlib.sha256(args.iscc.read_bytes()).hexdigest(),
        "checks": [],
    }
    data = work / "dados-ficticios-preservar.txt"
    data.write_text("dados ficticios externos", encoding="utf-8")
    try:
        for number in (1, 2, 2):
            release = f"0.0.{number}"
            payload = work / f"payload-{release}.txt"
            payload.write_text("payload " + release, encoding="utf-8")
            run(
                [
                    str(args.iscc),
                    "--quiet",
                    "--no-ide-signtools",
                    "--define=PrototypeId=" + identity,
                    "--define=PayloadVersion=" + release,
                    "--define=PayloadFile=" + str(payload),
                    "--define=WorkDir=" + str(work),
                    str(ROOT / "installer/prototipo.iss"),
                ],
                work / ("compile-" + release + "-" + uuid.uuid4().hex + ".txt"),
            )
            setup_log = work / ("setup-" + release + "-" + uuid.uuid4().hex + ".txt")
            run(
                [
                    str(work / ("prototipo-" + release + ".exe")),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/LOG=" + str(setup_log),
                ]
            )
            if not (install / "releases" / release / "payload.txt").is_file():
                raise AssertionError("Payload nao instalado no diretorio versionado.")
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                registry,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                if winreg.QueryValueEx(key, "DisplayVersion")[0] != release:
                    raise AssertionError("Versao de desinstalacao incorreta.")
            if not (group / "Payload.lnk").is_file():
                raise AssertionError("Atalho por usuario ausente.")
            if "EDISK_PROTOTYPE nonadmin=yes" not in setup_log.read_text(encoding="utf-8-sig"):
                raise AssertionError("Modo por usuario nao confirmado pelo setup.")
            report["checks"].append("install-" + release)
            if number == 1:
                (install / "desconhecido.txt").write_text("preservar", encoding="utf-8")
                (install / "releases/0.0.1/desconhecido.txt").write_text(
                    "preservar", encoding="utf-8"
                )
        uninstaller = install / "unins000.exe"
        if not uninstaller.is_file() or (install / "unins001.exe").exists():
            raise AssertionError("Log de desinstalacao nao acumulado no mesmo produto.")
        if not (install / "releases/0.0.1/payload.txt").is_file():
            raise AssertionError("Versao anterior removida antes da desinstalacao.")
        run(
            [
                str(uninstaller),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/LOG=" + str(work / "uninstall.txt"),
            ]
        )
        for release in ("0.0.1", "0.0.2"):
            if (install / "releases" / release / "payload.txt").exists():
                raise AssertionError("Arquivo conhecido permaneceu apos desinstalar.")
        for relative in ("desconhecido.txt", "releases/0.0.1/desconhecido.txt"):
            if (install / relative).read_text(encoding="utf-8") != "preservar":
                raise AssertionError("Arquivo desconhecido nao preservado.")
        if (group / "Payload.lnk").exists():
            raise AssertionError("Atalho nao removido.")
        try:
            winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                registry,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ).Close()
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Registro de desinstalacao nao removido.")
        if data.read_text(encoding="utf-8") != "dados ficticios externos":
            raise AssertionError("Dados externos alterados.")
        report["checks"] += [
            "append-uninstall-log",
            "uninstall-known-files",
            "preserve-unknown-files",
            "preserve-external-data",
            "remove-shortcut",
            "remove-uninstall-registration",
        ]
        report["status"] = "passed"
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Relatorio: " + str(work / "report.json"))
    # Residuos ficticios e relatorios permanecem para inspecao. Sem limpeza recursiva.


if __name__ == "__main__":
    main()
