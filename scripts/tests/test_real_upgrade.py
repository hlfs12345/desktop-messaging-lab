"""Atualizacao real Flutter/FastAPI A-B, GUI e corrida concorrente em instancia exclusiva."""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import time
import uuid
import winreg
from contextlib import closing
from ctypes import wintypes
from pathlib import Path

from gui_input import click_field, keys
from installer_attempt import run_attempt
from test_installer_native import launcher_failure
from test_supervisor import KERNEL, ProcessHandle, descendants
from test_windows_app import USER, capture, listening_backend, wait_for, window_for

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


def require(value, message):
    if not value:
        raise AssertionError(message)


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_attempt(executable, success=True):
    result = run_attempt(
        [str(executable), *FLAGS],
        capture_output=True,
        timeout=150,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    require(
        (result.returncode == 0) == success,
        "Resultado de manutencao inesperado: " + str(result.returncode),
    )
    return result.returncode


def member_handles(process, install, diagnostics=None):
    # A consulta ao Job e encerrada antes de retornar: nao retem KILL_ON_JOB_CLOSE.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.OpenJobObjectW(4, False, process.edisk_job_name)
    require(job, "Job exclusivo da instancia indisponivel.")
    handles = []
    try:
        capacity = 512
        buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        require(
            kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), None),
            "Nao foi possivel listar membros do Job.",
        )
        assigned, count = struct.unpack_from("<II", buffer.raw)
        require(assigned == count and count <= capacity, "Lista de membros incompleta.")
        pids = set(struct.unpack_from("<" + "Q" * count, buffer.raw, 8))
        for pid in pids:
            value = kernel.OpenProcess(0x101001, False, pid)
            require(value, "Membro do Job nao pode ser observado.")
            member = wintypes.BOOL()
            if not kernel.IsProcessInJob(value, job, ctypes.byref(member)) or not member.value:
                kernel.CloseHandle(value)
                raise AssertionError("Identidade de membro do Job divergiu.")
            item = ProcessHandle.__new__(ProcessHandle)
            item.value = value
            item.pid = pid
            handles.append(item)
        # Ancestralidade isolada pode incluir PIDs antigos ou auxiliares externos.
        # Um executavel instalado fora do Job e sempre falha; nunca e ignorado.
        for pid in descendants(process.pid) - pids:
            value = kernel.OpenProcess(0x101000, False, pid)
            if not value:
                continue
            try:
                image = ctypes.create_unicode_buffer(32768)
                length = wintypes.DWORD(len(image))
                if kernel.QueryFullProcessImageNameW(value, 0, image, ctypes.byref(length)):
                    member = wintypes.BOOL()
                    require(
                        kernel.IsProcessInJob(value, job, ctypes.byref(member)),
                        "Associacao de processo nao pode ser consultada.",
                    )
                    if member.value:
                        # Pode ter nascido entre as duas consultas do Windows.
                        owned = kernel.OpenProcess(0x101001, False, pid)
                        if owned:
                            checked = wintypes.BOOL()
                            if (
                                kernel.IsProcessInJob(owned, job, ctypes.byref(checked))
                                and checked.value
                            ):
                                item = ProcessHandle.__new__(ProcessHandle)
                                item.value = owned
                                item.pid = pid
                                handles.append(item)
                            else:
                                kernel.CloseHandle(owned)
                                raise AssertionError("Associacao de membro mudou.")
                        continue
                    require(
                        not Path(image.value).is_relative_to(install),
                        "Executavel instalado escapou do Job.",
                    )
                    if diagnostics is not None:
                        diagnostics.append(
                            {"pid": pid, "image": Path(image.value).name, "outside_job": True}
                        )
            finally:
                kernel.CloseHandle(value)
        return handles
    except BaseException:
        for item in handles:
            item.close()
        raise
    finally:
        kernel.CloseHandle(job)


def member_pids(process, install):
    # Startup e dinamico: somente a lista atomica do Job orienta a espera da GUI.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.OpenJobObjectW(4, False, process.edisk_job_name)
    if not job:
        return set()
    try:
        buffer = ctypes.create_string_buffer(8 + 512 * ctypes.sizeof(ctypes.c_size_t))
        if not kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), None):
            return set()
        _, count = struct.unpack_from("<II", buffer.raw)
        require(count <= 512, "Lista de membros maior que o limite de ensaio.")
        return set(struct.unpack_from("<" + "Q" * count, buffer.raw, 8))
    finally:
        kernel.CloseHandle(job)


def member_handle(process, install, pid):
    handles = member_handles(process, install)
    selected = None
    for item in handles:
        if item.pid == pid:
            selected = item
        else:
            item.close()
    require(selected is not None, "Processo esperado nao pertence ao Job.")
    return selected


class ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("id", wintypes.DWORD),
        ("owner", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("delta", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


def suspend_owned(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.SuspendThread.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    require(snapshot != ctypes.c_void_p(-1).value, "Snapshot de threads falhou.")
    entry = ThreadEntry()
    entry.size = ctypes.sizeof(entry)
    handles = []
    try:
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.owner == pid:
                handle = kernel.OpenThread(2, False, entry.id)
                require(
                    handle and kernel.SuspendThread(handle) != 0xFFFFFFFF,
                    "Suspensao da thread de ensaio falhou.",
                )
                handles.append(handle)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    require(handles, "Backend de ensaio sem threads.")
    return kernel, handles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", "--artifact", type=Path, required=True)
    parser.add_argument("--b", "--artifact-b", type=Path, required=True)
    args = parser.parse_args()
    require(not ctypes.windll.shell32.IsUserAnAdmin(), "Ensaio exige usuario nao elevado.")
    a = json.loads((args.a / "installer-report.json").read_text())
    b = json.loads((args.b / "installer-report.json").read_text())
    require(
        a["input"]["AppId"] == b["input"]["AppId"]
        and a["input"]["AppId"].startswith("eDisk-Ensaio-"),
        "AppId deve ser exclusivo e igual.",
    )
    require(a["input"]["Channel"] == b["input"]["Channel"] == "tecnico", "Somente tecnico.")
    require(
        a["input"]["AppVersion"] != b["input"]["AppVersion"],
        "A e B devem ser versoes reais diferentes.",
    )
    setups = [next(folder.glob("*.exe")) for folder in (args.a, args.b)]
    for exe, meta in zip(setups, (a, b), strict=True):
        require(checksum(exe) == meta["installer_sha256"], "Digest do setup diverge.")
    folder = a["input"]["InstallFolder"]
    root = Path(os.environ["LOCALAPPDATA"]) / "Programs" / folder
    data = Path(os.environ["LOCALAPPDATA"]) / "eDisk-validacao-instalador" / folder
    require(not root.exists() and not data.exists(), "Instancia deve ser nova.")
    work = ROOT / "build/real-upgrade" / uuid.uuid4().hex
    work.mkdir(parents=True)
    password = "Ensaio-" + uuid.uuid4().hex
    database = data / "data/edisk.sqlite3"
    report = {
        "status": "failed",
        "checks": [],
        "a": a["id"],
        "b": b["id"],
        "data": str(data),
        "test_script_sha256": checksum(Path(__file__).resolve()),
        "a_setup_sha256": a["installer_sha256"],
        "b_setup_sha256": b["installer_sha256"],
        "executions": [],
    }
    running = []

    registry_key = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + a["input"]["AppId"] + "_is1"
    )

    def registration():
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, registry_key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            path = Path(winreg.QueryValueEx(key, "UninstallString")[0].strip('"'))
            version = winreg.QueryValueEx(key, "DisplayVersion")[0]
        require(
            path.parent == root and path.is_file(), "Desinstalador registrado fora da instancia."
        )
        report.setdefault("uninstall_registrations", []).append(
            {"version": version, "executable": path.name}
        )
        return path, version

    def uninstall_operation(success=True):
        executable, _ = registration()
        result = install_attempt(executable, success=success)
        if success:

            def removed():
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        registry_key,
                        0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                    ):
                        return False
                except FileNotFoundError:
                    return not executable.exists()

            wait_for(removed)
        return result

    def count(table, where="1=1"):
        if not database.exists():
            return -1
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                return connection.execute(
                    "SELECT count(*) FROM " + table + " WHERE " + where
                ).fetchone()[0]
        except sqlite3.Error:
            return -1

    def start(test=False):
        launcher = root / "edisk-launcher.exe"
        if test:
            shutil.copyfile(
                ROOT / "build/installer-native/Release/edisk-launcher-test.exe", launcher
            )
        previous = set((root / "runtime").glob("*.claim")) if (root / "runtime").exists() else set()
        process = subprocess.Popen([str(launcher)])
        running.append(process)
        claim = wait_for(
            lambda: next(iter(set((root / "runtime").glob("*.claim")) - previous), None)
        )
        process.edisk_job_name = "Local\\eDisk-" + claim.stem
        process.edisk_claim = claim
        report.setdefault("coordinators", []).append({"pid": process.pid, "claim": claim.name})
        hwnd, runner = wait_for(lambda: window_for(member_pids(process, root)))
        backend = wait_for(lambda: listening_backend(member_pids(process, root)))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        active = (root / "active.txt").read_text(encoding="ascii").splitlines()
        for pid, relative in ((runner, "edisk.exe"), (backend, "backend/edisk-backend.exe")):
            handle = kernel.OpenProcess(0x1000, False, pid)
            require(handle, "Processo de ensaio inacessivel.")
            try:
                image = ctypes.create_unicode_buffer(32768)
                length = wintypes.DWORD(len(image))
                require(
                    kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)),
                    "Imagem do processo indisponivel.",
                )
                expected = root / "releases" / active[3] / relative
                require(Path(image.value).samefile(expected), "Processo executou outro payload.")
            finally:
                kernel.CloseHandle(handle)
        report["executions"].append(
            {
                "version": active[2],
                "release": active[3],
                "runner_sha256": checksum(root / "releases" / active[3] / "edisk.exe"),
            }
        )
        time.sleep(1)
        return process, hwnd, runner, backend

    def close(process, hwnd):
        handles = member_handles(process, root, report.setdefault("outside_job_ancestry", []))
        try:
            USER.PostMessageW(hwnd, 0x10, 0, 0)
            require(process.wait(timeout=25) == 0, "Coordenador nao confirmou fechamento.")
            states = [
                {"pid": handle.pid, "wait": KERNEL.WaitForSingleObject(handle.value, 0)}
                for handle in handles
            ]
            report.setdefault("closures", []).append(
                {"coordinator_exit": process.returncode, "members": states}
            )
            require(
                all(item["wait"] == 0 for item in states),
                "Membro do Job ainda vivo ou handle invalido.",
            )
            for claim in (root / "runtime").glob("*.claim"):
                lines = claim.read_bytes().splitlines()
                require(
                    len(lines) == 5 and lines[0] == b"EDISK-CONFIRMED-3",
                    "Descendentes nao confirmados.",
                )
                require(
                    hashlib.sha256(b"\r\n".join(lines[:4]) + b"\r\n").hexdigest().encode()
                    == lines[4],
                    "Confirmacao de encerramento corrompida.",
                )
        finally:
            for handle in handles:
                handle.close()

    try:
        install_attempt(setups[0])
        original_uninstaller, registered_version = registration()
        require(registered_version == a["input"]["AppVersion"], "Registro de A diverge.")
        require(not data.exists(), "Setup criou dados.")
        process, hwnd, _, _ = start()
        wait_for(lambda: count("usuarios") == 0)
        for y, value in zip(
            (255, 320, 405, 490),
            ("Pessoa Ficticia", "ensaio.instalado", password, password),
            strict=True,
        ):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)
        wait_for(lambda: count("usuarios") == 1)
        time.sleep(1)
        for y, value in zip((225, 310), ("ensaio.instalado", password), strict=True):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)
        wait_for(lambda: count("sessoes", "revogado_em IS NULL") == 1)
        capture(hwnd, "entrega8-correcao-A-autenticado.png")
        close(process, hwnd)
        report["checks"].append("real-A-GUI-bootstrap-and-login")
        for name in ("backups/sentinela-ficticia.txt", "logs/sentinela-ficticia.txt"):
            path = data / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("preservar " + name, encoding="ascii")
        before = checksum(database)
        install_attempt(setups[1])
        updated_uninstaller, registered_version = registration()
        require(registered_version == b["input"]["AppVersion"], "Registro de B diverge.")
        require(
            updated_uninstaller == original_uninstaller,
            "Atualizacao nao acumulou o mesmo log Inno.",
        )
        require(checksum(database) == before, "Atualizacao alterou banco.")
        require(
            (root / "active.txt").read_text().splitlines()[2] == b["input"]["AppVersion"],
            "B nao esta ativa.",
        )
        for name in ("backups/sentinela-ficticia.txt", "logs/sentinela-ficticia.txt"):
            require(
                (data / name).read_text(encoding="ascii") == "preservar " + name,
                "Atualizacao removeu sentinela.",
            )
        for meta in (a, b):
            release = root / "releases" / meta["input"]["ReleaseId"]
            inventory = (release / "inventory.txt").read_text().splitlines()
            expected = {}
            for line in inventory:
                digest, name = line.split("  ", 1)
                expected[name] = digest
            actual = {
                p.relative_to(release).as_posix(): checksum(p)
                for p in release.rglob("*")
                if p.is_file() and p.name != "inventory.txt"
            }
            require(actual == expected, "Mistura/alteracao de arquivos de payload.")
        process, hwnd, _, _ = start()
        wait_for(lambda: count("sessoes", "revogado_em IS NULL") == 0)
        for y, value in zip((225, 310), ("ensaio.instalado", password), strict=True):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)
        wait_for(lambda: count("sessoes", "revogado_em IS NULL") == 1)
        require(count("usuarios") == 1, "Administrador nao preservado.")
        capture(hwnd, "entrega8-correcao-B-autenticado.png")
        close(process, hwnd)
        report["checks"].append("real-B-GUI-login-auth-db-backups-logs-no-mixed-payload")

        # Pausa somente no coordenador de ensaio; backend real nao recebe hook.
        process, hwnd, runner_pid, backend_pid = start(test=True)
        runner = member_handle(process, root, runner_pid)
        backend = member_handle(process, root, backend_pid)
        kernel, threads = suspend_owned(backend_pid)
        try:
            runner.kill()
            require(
                KERNEL.WaitForSingleObject(runner.value, 5000) == 0,
                "Morte do runner nao confirmada.",
            )
            runner_dead_ns = time.monotonic_ns()
            attempt_start_ns = time.monotonic_ns()
            # Disputa iniciada imediatamente, SEM esperar backend morrer ou consultar sua morte.
            result = install_attempt(setups[1], success=False)
            wait_for(lambda: (root / "runtime/test-pause").exists())
            require(
                KERNEL.WaitForSingleObject(backend.value, 0) == 258,
                "Backend nao estava vivo na janela critica.",
            )
            uninstall_operation(success=False)
            require(
                KERNEL.WaitForSingleObject(backend.value, 0) == 258,
                "Backend morreu antes da disputa de desinstalacao.",
            )
            report["race"] = {
                "runner_terminated": True,
                "maintenance_delay_after_runner_ns": attempt_start_ns - runner_dead_ns,
                "backend_alive_during_setup": True,
                "setup_exit": result,
                "uninstall_blocked": True,
            }
            (root / "runtime/test-release").touch()
            report["race"]["launcher_diagnostic"] = launcher_failure(process, 5, 1, "aplicativo")
            report["race"]["launcher_exit"] = process.returncode
            require(
                KERNEL.WaitForSingleObject(backend.value, 0) == 0, "Backend vivo apos confirmacao."
            )
        finally:
            for thread in threads:
                kernel.CloseHandle(thread)
            runner.close()
            backend.close()
        (root / "runtime/test-pause").unlink()
        (root / "runtime/test-release").unlink()
        install_attempt(setups[1])
        report["checks"].append("runner-death-concurrent-maintenance-backend-alive-then-confirmed")

        process, hwnd, runner_pid, backend_pid = start(test=True)
        handles = member_handles(process, root, report.setdefault("outside_job_ancestry", []))
        runner = member_handle(process, root, runner_pid)
        kernel, threads = suspend_owned(backend_pid)
        try:
            runner.kill()
            wait_for(lambda: (root / "runtime/test-pause").exists())
            process.kill()
            process.wait(timeout=5)
            require(all(handle.dead() for handle in handles), "Descendente de ensaio nao encerrou.")
            require(
                list((root / "runtime").glob("*.claim")), "Perda de confirmacao apagou marcador."
            )
            install_attempt(setups[1], success=False)
            uninstall_operation(success=False)
            # Recuperacao exclusiva da fixture, com todos os handles exatos sinalizados acima.
            for path in (root / "runtime").iterdir():
                path.unlink()
        finally:
            for thread in threads:
                kernel.CloseHandle(thread)
            runner.close()
            for handle in handles:
                handle.close()
        install_attempt(setups[1])
        report["checks"].append("lost-coordinator-confirmation-fails-closed-after-backend-death")
        (root / "test-retire-mode").touch()
        process, hwnd, _, _ = start(test=True)
        retirement_handles = member_handles(process, root)
        try:
            USER.PostMessageW(hwnd, 0x10, 0, 0)
            wait_for(lambda: (root / "test-retire-pause").exists())
            require(
                all(
                    KERNEL.WaitForSingleObject(handle.value, 0) == 0
                    for handle in retirement_handles
                ),
                "Descendente vivo no encerramento do coordenador.",
            )
            require(process.poll() is None, "Coordenador de ensaio nao estava vivo.")
            # Prova que nao e apenas a trava anterior: nenhum leitor impede abertura de escrita.
            with (root / "maintenance.lock").open("r+b"):
                pass
            install_attempt(setups[1], success=False)
            uninstall_operation(success=False)
            (root / "test-retire-release").touch()
            require(process.wait(timeout=10) == 0, "Coordenador nao concluiu aposentadoria.")
        finally:
            for handle in retirement_handles:
                handle.close()
        for name in ("test-retire-mode", "test-retire-pause", "test-retire-release"):
            (root / name).unlink()
        install_attempt(setups[1])
        report["checks"].append(
            "coordinator-alive-after-lease-release-blocks-maintenance-until-process-end"
        )
        before = checksum(database)
        uninstall_operation()
        require(checksum(database) == before, "Desinstalacao alterou banco.")
        for name in ("backups/sentinela-ficticia.txt", "logs/sentinela-ficticia.txt"):
            require(
                (data / name).read_text(encoding="ascii") == "preservar " + name,
                "Sentinela removida.",
            )
        for meta in (a, b):
            require(
                not (root / "releases" / meta["input"]["ReleaseId"] / "edisk.exe").exists(),
                "Log Inno nao removeu versao conhecida.",
            )
        report["checks"].append("uninstall-both-releases-preserves-database-backups-logs")
        # Reinstalacao real de B conserva administrador e requer novo login.
        install_attempt(setups[1])
        process, hwnd, _, _ = start()
        wait_for(lambda: count("sessoes", "revogado_em IS NULL") == 0)
        for y, value in zip((225, 310), ("ensaio.instalado", password), strict=True):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)
        wait_for(lambda: count("sessoes", "revogado_em IS NULL") == 1)
        require(count("usuarios") == 1, "Reinstalacao perdeu administrador.")
        close(process, hwnd)
        before = checksum(database)
        uninstall_operation()
        require(checksum(database) == before, "Segunda desinstalacao alterou banco.")
        report["checks"].append("reinstall-B-requires-existing-login")
        report["status"] = "passed"
    finally:
        for process in running:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Relatorio: " + str(work / "report.json"))


if __name__ == "__main__":
    main()
