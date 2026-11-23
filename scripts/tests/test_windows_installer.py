"""Ciclo real Inno com payload nativo ficticio e AppId descartavel, sem banco real."""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
import winreg
from ctypes import wintypes
from pathlib import Path

from installer_attempt import prepare_attempt
from pe_fixture import variant
from test_installer_native import launcher_failure
from test_supervisor import ProcessHandle, descendants

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "build/installer-native/Release"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError("Condicao nao alcancada no prazo do ensaio.")


def run(command, log=None, success=True):
    from installer_attempt import run_attempt

    executor = run_attempt if "/VERYSILENT" in command else subprocess.run
    result = executor(
        command,
        capture_output=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if log:
        log.write_bytes(result.stdout + result.stderr)
    if success and result.returncode:
        raise RuntimeError(f"Comando retornou {result.returncode}; consultar {log}.")
    return result.returncode


def fixture(iscc, work, identity, number):
    stage = work / str(number)
    payload = stage / "payload"
    (payload / "backend").mkdir(parents=True)
    shutil.copyfile(NATIVE / "fixture_runner.exe", payload / "edisk.exe")
    shutil.copyfile(NATIVE / "edisk-launcher.exe", payload / "edisk-launcher.exe")
    (payload / "backend/edisk-backend.exe").write_bytes(b"fixture nao executavel")
    (payload / "build-info.json").write_text("{}", encoding="ascii")
    (payload / "THIRD_PARTY_NOTICES.md").write_text("Payload ficticio de teste.", encoding="ascii")
    (payload / f"only-{number}.txt").write_text(str(number), encoding="ascii")
    version = f"0.0.1.{number}"
    release = version + "-abcdef"
    variant(payload / "edisk.exe", version)
    (payload / "manifest-sha256.json").write_text("{}", encoding="ascii")
    approved = (
        "\n".join(
            [
                "EDISK-PAYLOAD-3",
                "tecnico",
                "bundled/test",
                version,
                release,
                hashlib.sha256(b"{}").hexdigest(),
                hashlib.sha256(b"{}").hexdigest(),
            ]
        )
        + "\n"
    )
    (payload / "approved-payload.txt").write_text(approved, encoding="ascii")
    hashes = {
        p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in payload.rglob("*")
        if p.is_file()
    }
    text = "".join(f"{value}  {name}\n" for name, value in sorted(hashes.items()))
    (payload / "inventory.txt").write_text(text, encoding="ascii")
    inventory_hash = hashlib.sha256((payload / "inventory.txt").read_bytes()).hexdigest()
    hashes["inventory.txt"] = inventory_hash
    version = f"0.0.1.{number}"
    release = version + "-abcdef"
    config = {
        "AppId": "eDisk-Ensaio-" + identity,
        "InstallFolder": "eDisk-Ensaio-a\u00e7\u00e3o " + identity,
        "AppVersion": version,
        "Channel": "tecnico",
        "ReleaseId": release,
        "InventoryHash": inventory_hash,
    }
    (stage / "config.iss").write_text(
        "\n".join(f'#define {k} "{v}"' for k, v in config.items()) + "\n#define TestBuild 1\n",
        encoding="utf-8",
    )
    entries = []
    checks = ["procedure VerifyPayload();", "begin"]
    for name, digest in hashes.items():
        source = payload / name
        target = "{app}\\releases\\{#ReleaseId}"
        if source.parent != payload:
            target += "\\" + source.parent.relative_to(payload).as_posix().replace("/", "\\")
        entries.append(f'Source: "{source}"; DestDir: "{target}"; Flags: ignoreversion')
        relative = name.replace("/", "\\")
        checks.append(
            f"  if GetSHA256OfFile(ExpandConstant('{{app}}\\releases\\{{#ReleaseId}}\\{relative}'))"
            f" <> '{digest}' then RaiseException('Fixture alterada.');"
        )
    checks.append("end;")
    entries.append(
        f'Source: "{payload / "edisk-launcher.exe"}"; DestDir: "{{app}}"; Flags: ignoreversion'
    )
    (stage / "files.iss").write_text("\n".join(entries), encoding="utf-8")
    (stage / "checks.iss").write_text("\n".join(checks), encoding="utf-8")
    run(
        [
            str(iscc),
            "--quiet",
            "--no-ide-signtools",
            "--define=Stage=" + str(stage),
            "--output-dir=" + str(stage),
            "--output-filename=setup",
            str(ROOT / "installer/edisk.iss"),
        ],
        stage / "compile.txt",
    )
    return stage / "setup.exe", release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, required=True)
    args = parser.parse_args()
    require(
        not ctypes.windll.shell32.IsUserAnAdmin(),
        "Ensaio exige token Windows nao elevado.",
    )
    identity = uuid.uuid4().hex
    work = ROOT / "build/installer-tests" / identity
    work.mkdir(parents=True)
    install = (
        Path(os.environ["LOCALAPPDATA"]) / "Programs" / ("eDisk-Ensaio-a\u00e7\u00e3o " + identity)
    )
    group = (
        Path(os.environ["APPDATA"])
        / "Microsoft/Windows/Start Menu/Programs"
        / ("eDisk-Ensaio-a\u00e7\u00e3o " + identity)
    )
    data = work / "dados ficticios acao"
    data.mkdir()
    (data / "preservar.txt").write_text("sentinela", encoding="ascii")
    report = {
        "status": "failed",
        "scope": "fixtures-nativas-sem-banco",
        "install": str(install),
        "checks": [],
    }
    try:
        setups = [fixture(args.iscc, work, identity, n) for n in range(1, 10)]

        def setup(number, *extra, success=True):
            return run(
                [
                    str(setups[number - 1][0]),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/LOG=" + str(work / f"setup-{number}-{uuid.uuid4().hex}.txt"),
                    *extra,
                ],
                success=success,
            )

        def verify():
            return run(
                [str(NATIVE / "guard_harness.exe"), "verify", str(install)],
                success=False,
            )

        setup(1)
        require(verify() == 0, "Primeira instalacao invalida.")
        require((group / "eDisk.lnk").is_file(), "Atalho estavel ausente.")
        registry = (
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\eDisk-Ensaio-" + identity + "_is1"
        )
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            registry,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ):
            pass
        report["checks"] += [
            "first-install-nonadmin",
            "shortcut",
            "uninstall-registration",
        ]
        setup(1)
        require(verify() == 0, "Reinstalacao invalida.")
        holder = subprocess.Popen(
            [str(NATIVE / "guard_harness.exe"), "hold", str(install)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            require(holder.stdout.readline().strip() == "LOCKED", "Trava nao adquirida.")
            require(setup(2, success=False) != 0, "Atualizacao permitida durante execucao.")
            require(verify() == 0, "Tentativa bloqueada alterou a instalacao.")
        finally:
            holder.kill()
            holder.wait(timeout=5)
            holder.stdin.close()
            holder.stdout.close()
        launcher = subprocess.Popen([str(install / "edisk-launcher.exe")])
        handles = []
        try:
            children = wait_for(lambda: descendants(launcher.pid))
            handles = [ProcessHandle(pid) for pid in children]
            time.sleep(0.15)
            launcher.kill()
            launcher.wait(timeout=5)
            require(
                setup(2, success=False) != 0,
                "Runner perdeu a trava apos morte do launcher.",
            )
        finally:
            for handle in handles:
                if not handle.dead():
                    handle.kill()
                    require(handle.dead(), "Fixture nao encerrou.")
                handle.close()
            if launcher.poll() is None:
                launcher.kill()
                launcher.wait(timeout=5)
        report["checks"].append("unconfirmed-claim-blocks-after-launcher-death")
        require(setup(2, success=False) != 0, "Confirmacao perdida liberou manutencao.")
        # Limpeza estritamente da fixture, apos handles exatos de todos os filhos sinalizados.
        for claim in (install / "runtime").glob("*.claim"):
            claim.unlink()
        # O proprio coordenador continua vivo depois de liberar sua trava.
        shutil.copyfile(NATIVE / "edisk-launcher-test.exe", install / "edisk-launcher.exe")
        (install / "test-retire-mode").touch()
        retiring = subprocess.Popen([str(install / "edisk-launcher.exe")])
        retired_handles = []
        try:
            children = wait_for(lambda: descendants(retiring.pid))
            retired_handles = [ProcessHandle(pid) for pid in children]
            for handle in retired_handles:
                handle.kill()
                require(handle.dead(), "Fixture nao terminou.")
            wait_for(lambda: (install / "test-retire-pause").exists())
            with (install / "maintenance.lock").open("r+b"):
                pass
            require(retiring.poll() is None, "Coordenador nao estava vivo.")
            require(
                setup(2, success=False) != 0, "Manutencao permitida antes do fim do coordenador."
            )
            require(
                run(
                    [
                        str(install / "unins000.exe"),
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                    ],
                    success=False,
                )
                != 0,
                "Desinstalacao permitida com coordenador ainda vivo.",
            )
            (install / "test-retire-release").touch()
            launcher_failure(retiring, 5, 1, "aplicativo")
        finally:
            for handle in retired_handles:
                handle.close()
            if retiring.poll() is None:
                retiring.kill()
                retiring.wait(timeout=5)
        for name in ("test-retire-mode", "test-retire-pause", "test-retire-release"):
            (install / name).unlink()
        retirement = next((install / "runtime").glob("*.claim"))
        original = retirement.read_bytes()
        lines = original.splitlines(keepends=True)
        lines[1] = lines[1].replace(b"\r\n", b"0\r\n")
        retirement.write_bytes(b"".join(lines))
        require(setup(2, success=False) != 0, "Estado de aposentadoria corrompido foi aceito.")
        retirement.write_bytes(original)
        setup(2)
        report["checks"] += [
            "coordinator-exit-barrier-without-reader-lease",
            "retirement-corruption-rejected",
        ]
        require(verify() == 0, "Atualizacao A-B invalida.")
        require(
            (install / "releases" / setups[0][1] / "only-1.txt").is_file(),
            "Versao anterior removida.",
        )
        require(setup(1, success=False) != 0, "Downgrade permitido.")
        report["checks"] += [
            "reinstall",
            "maintenance-exclusion",
            "abrupt-reader-release",
            "update-A-B",
            "retain-old-release",
            "reject-downgrade",
        ]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        target = install / "releases" / setups[2][1] / "edisk.exe"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"arquivo de ensaio bloqueado")
        lock = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0x80, None)
        require(lock != ctypes.c_void_p(-1).value, "Nao foi possivel bloquear fixture.")
        try:
            require(setup(3, success=False) != 0, "Falha real de copia nao foi detectada.")
            require(verify() != 0, "Falha de copia liberou execucao.")
        finally:
            kernel.CloseHandle(lock)
        setup(3)
        require(verify() == 0, "Reparacao apos falha real de copia falhou.")
        report["checks"].append("real-copy-failure-repair")
        for number, point in (
            (3, "copy"),
            (4, "before-activation"),
            (5, "after-activation"),
            (6, "finalize"),
        ):
            setup(number, "/EDISKFAIL=" + point, success=False)
            require(
                (install / "pending.txt").is_file(),
                "Falha nao conservou marcador de manutencao.",
            )
            require(verify() != 0, "Instalacao incompleta liberada para execucao.")
            setup(number)
            require(verify() == 0, "Reparacao apos falha nao recuperou a instalacao.")
            report["checks"].append("repair-" + point)
        for number, point in (
            (7, "before-activation"),
            (8, "after-activation"),
            (9, "finalize"),
        ):
            attempt_command = [
                str(setups[number - 1][0]),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/EDISKPAUSE=" + point,
            ]
            attempt_command, attempt_folder, attempt_receipt = prepare_attempt(attempt_command)
            process = subprocess.Popen(attempt_command)
            handles = []
            try:
                wait_for(lambda: (install / "test-pause.txt").is_file())
                handles = [ProcessHandle(pid) for pid in descendants(process.pid)]
                for handle in handles:
                    handle.kill()
                    require(handle.dead(), "Setup de ensaio nao encerrou.")
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            finally:
                for handle in handles:
                    handle.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            require(
                (install / "pending.txt").is_file(),
                "Interrupcao perdeu marcador pendente.",
            )
            require(verify() != 0, "Interrupcao liberou execucao.")
            setup(number)
            require(verify() == 0, "Reparacao apos interrupcao falhou.")
            report["checks"].append("abrupt-setup-" + point)
        # Arquivos desconhecidos na raiz e numa versao antiga pertencem ao usuario.
        (install / "desconhecido.txt").write_text("preservar", encoding="ascii")
        unknown = install / "releases" / setups[0][1] / "desconhecido.txt"
        unknown.write_text("preservar", encoding="ascii")
        run(
            [
                str(install / "unins000.exe"),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/LOG=" + str(work / "uninstall.txt"),
            ]
        )
        for _, release in setups:
            require(
                not (install / "releases" / release / "edisk.exe").exists(),
                "Payload conhecido residual.",
            )
        require(
            unknown.read_text(encoding="ascii") == "preservar",
            "Arquivo desconhecido removido.",
        )
        require(
            (install / "desconhecido.txt").read_text(encoding="ascii") == "preservar",
            "Arquivo da raiz removido.",
        )
        require(not (group / "eDisk.lnk").exists(), "Atalho residual.")
        require(
            (data / "preservar.txt").read_text(encoding="ascii") == "sentinela",
            "Dados ficticios alterados.",
        )
        report["checks"] += [
            "uninstall-all-releases",
            "preserve-unknown",
            "preserve-external-data",
        ]
        setup(9)
        require(verify() == 0, "Reinstalacao apos desinstalar falhou.")
        require(unknown.is_file(), "Reinstalacao alterou arquivo desconhecido.")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            registry,
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            uninstaller = winreg.QueryValueEx(key, "UninstallString")[0].strip('"')
        require(Path(uninstaller).parent == install, "Desinstalador fora da raiz de ensaio.")
        run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        report["checks"].append("reinstall-after-uninstall")
        report["status"] = "passed"
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Relatorio: " + str(work / "report.json"))


if __name__ == "__main__":
    main()
