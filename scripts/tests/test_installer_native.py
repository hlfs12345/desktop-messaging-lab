"""Inventario e compartilhamento Windows reais, sem banco ou SDK Flutter."""

import ctypes
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import uuid
from ctypes import wintypes
from pathlib import Path

from pe_fixture import variant

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "build/installer-native/Release/guard_harness.exe"


def launcher_failure(process, expected_exit, expected_detail=None, label="Windows"):
    """Le e fecha somente a caixa de erro pertencente ao launcher de ensaio."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    dialogs = []

    @callback
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ctypes.create_unicode_buffer(64)
        user.GetClassNameW(hwnd, name, len(name))
        if pid.value == process.pid and name.value == "#32770" and user.IsWindowVisible(hwnd):
            dialogs.append(hwnd)
        return True

    deadline = time.monotonic() + 20
    while not dialogs and process.poll() is None and time.monotonic() < deadline:
        user.EnumWindows(visit, 0)
        time.sleep(0.01)
    if len(dialogs) != 1:
        raise AssertionError("Diagnostico do launcher ausente ou ambiguo.")
    texts = []

    @callback
    def child(hwnd, _):
        value = ctypes.create_unicode_buffer(2048)
        user.GetWindowTextW(hwnd, value, len(value))
        if value.value:
            texts.append(value.value)
        return True

    while not texts and time.monotonic() < deadline:
        user.EnumChildWindows(dialogs[0], child, 0)
        time.sleep(0.01)
    message = "\n".join(texts)
    # Confirma o dialogo mesmo se uma assercao abaixo falhar.
    assert user.PostMessageW(dialogs[0], 0x10, 0, 0)
    result = process.wait(timeout=10)
    if result != expected_exit:
        raise AssertionError(f"Saida {result}; esperada {expected_exit}.")
    if expected_detail is not None:
        assert f"C\u00f3digo do {label}: {expected_detail}." in message, repr(message)
    return message


class InstallationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edisk-guarda-acao-")
        self.root = Path(self.temp.name)
        self.payload = self.root / "releases/0.1.0.1-aabbcc"
        self.payload.mkdir(parents=True)
        (self.root / "maintenance.lock").touch()
        (self.root / "installation.id").write_text("EDISK-INSTALL-3\ntecnico\n", encoding="ascii")
        (self.payload / "backend").mkdir()
        shutil.copyfile(
            ROOT / "build/installer-native/Release/fixture_runner.exe",
            self.payload / "edisk.exe",
        )
        variant(self.payload / "edisk.exe", "0.1.0.1")
        for name in (
            "backend/edisk-backend.exe",
            "build-info.json",
            "manifest-sha256.json",
        ):
            (self.payload / name).write_bytes(b"fixture")
        approved = (
            "\n".join(
                [
                    "EDISK-PAYLOAD-3",
                    "tecnico",
                    "bundled/test",
                    "0.1.0.1",
                    "0.1.0.1-aabbcc",
                    hashlib.sha256(b"fixture").hexdigest(),
                    hashlib.sha256(b"fixture").hexdigest(),
                ]
            )
            + "\n"
        )
        (self.payload / "approved-payload.txt").write_text(approved, encoding="ascii")
        self.inventory()

    def inventory(self):
        records = [
            hashlib.sha256(p.read_bytes()).hexdigest()
            + "  "
            + p.relative_to(self.payload).as_posix()
            + "\n"
            for p in self.payload.rglob("*")
            if p.is_file() and p.name != "inventory.txt"
        ]
        (self.payload / "inventory.txt").write_text("".join(records), encoding="ascii")
        checksum = hashlib.sha256((self.payload / "inventory.txt").read_bytes()).hexdigest()
        (self.root / "active.txt").write_text(
            "EDISK-ACTIVE-1\ntecnico\n0.1.0.1\n0.1.0.1-aabbcc\n" + checksum + "\n",
            encoding="ascii",
        )

    def tearDown(self):
        self.temp.cleanup()

    def verify(self):
        return subprocess.run(
            [str(HARNESS), "verify", str(self.root)], capture_output=True, timeout=5
        ).returncode

    def test_valid_and_modified_payload(self):
        self.assertEqual(self.verify(), 0)
        (self.payload / "edisk.exe").write_bytes(b"alterado")
        self.assertNotEqual(self.verify(), 0)

    def test_extra_missing_and_pending(self):
        extra = self.payload / "extra.txt"
        extra.touch()
        self.assertNotEqual(self.verify(), 0)
        extra.unlink()
        (self.root / "pending.txt").write_text("pendente", encoding="ascii")
        self.assertNotEqual(self.verify(), 0)
        (self.root / "pending.txt").unlink()
        (self.payload / "edisk.exe").unlink()
        self.assertNotEqual(self.verify(), 0)

    def test_path_escape(self):
        text = (self.root / "active.txt").read_text(encoding="ascii")
        (self.root / "active.txt").write_text(
            text.replace("0.1.0.1-aabbcc", "../escape"), encoding="ascii"
        )
        self.assertNotEqual(self.verify(), 0)

    def test_active_version_and_channel_contradictions(self):
        path = self.root / "active.txt"
        original = path.read_text(encoding="ascii")
        for before, after in (("0.1.0.1\n", "0.1.0.2\n"), ("tecnico", "candidato")):
            path.write_text(original.replace(before, after), encoding="ascii")
            self.assertNotEqual(self.verify(), 0)
        path.write_text(original, encoding="ascii")
        self.assertEqual(self.verify(), 0)

    def start_launcher(self):
        target = self.root / "edisk-launcher.exe"
        if not target.exists():
            shutil.copyfile(ROOT / "build/installer-native/Release/edisk-launcher.exe", target)
        return subprocess.Popen([str(target)])

    def assert_released(self):
        with (self.root / "maintenance.lock").open("r+b"):
            pass
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenJobObjectW.restype = wintypes.HANDLE
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        for claim in (self.root / "runtime").glob("*.claim"):
            self.assertEqual(claim.read_text(encoding="ascii").splitlines()[0], "EDISK-CONFIRMED-3")
            name = "Local\\eDisk-" + claim.stem
            for opener, suffix in (
                (kernel.OpenJobObjectW, ""),
                (kernel.OpenJobObjectW, "-backend"),
                (kernel.OpenEventW, "-retained"),
            ):
                handle = opener(0x100000, False, name + suffix)
                error = ctypes.get_last_error()
                if handle:
                    kernel.CloseHandle(handle)
                self.assertFalse(handle, "Objeto de coordenacao ainda existe.")
                self.assertEqual(error, 2)

    def test_real_create_process_failure_and_recovery(self):
        executable = self.payload / "edisk.exe"
        original = executable.read_bytes()
        data = bytearray(original)
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        characteristics = struct.unpack_from("<H", data, pe + 22)[0]
        # PE valido para leitura de recursos, mas DLL nao pode ser iniciada como processo.
        struct.pack_into("<H", data, pe + 22, characteristics | 0x2000)
        executable.write_bytes(data)
        self.inventory()
        self.assertEqual(self.verify(), 0, "Falha deve ocorrer depois da validacao.")
        with self.assertRaises(OSError) as refused:
            subprocess.Popen([str(executable)])
        self.assertEqual(refused.exception.winerror, 193)
        (self.root / "test-runner-exit.txt").write_text("0", encoding="ascii")
        process = self.start_launcher()
        try:
            message = launcher_failure(process, 4, 193)
            self.assertIn("N\u00e3o foi poss\u00edvel iniciar o eDisk.", message)
            self.assertNotIn(str(self.root), message)
            self.assertFalse((self.root / "test-runner-started.txt").exists())
            self.assert_released()
            # A mesma instalacao volta a funcionar; nenhum fallback ou reparo artificial.
            executable.write_bytes(original)
            self.inventory()
            recovered = self.start_launcher()
            self.assertEqual(recovered.wait(timeout=20), 0)
            self.assertEqual((self.root / "test-runner-started.txt").read_text(), "1")
            self.assert_released()
            work = ROOT / "build/launcher-startup" / uuid.uuid4().hex
            work.mkdir(parents=True)
            (work / "report.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "scope": "fixture-native-no-production-data",
                        "create_process_winerror": 193,
                        "launcher_exit": 4,
                        "diagnostic": message,
                        "runner_started_on_failure": False,
                        "coordination_objects_gone": True,
                        "maintenance_lock_released": True,
                        "backend_not_started": True,
                        "recovered_exit": recovered.returncode,
                        "launcher_sha256": hashlib.sha256(
                            (self.root / "edisk-launcher.exe").read_bytes()
                        ).hexdigest(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print("Evidencia do launcher: " + str(work / "report.json"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_runner_error_is_not_launcher_success(self):
        (self.root / "test-runner-exit.txt").write_text("37", encoding="ascii")
        process = self.start_launcher()
        try:
            message = launcher_failure(process, 5, 37, "aplicativo")
            self.assertIn("O eDisk encerrou com erro.", message)
            self.assertTrue((self.root / "test-runner-started.txt").exists())
            self.assert_released()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_preparation_failure_is_not_silent(self):
        (self.root / "runtime").write_bytes(b"fixture")
        process = self.start_launcher()
        try:
            message = launcher_failure(process, 2)
            self.assertIn("abertura segura do eDisk", message)
            self.assertFalse((self.root / "test-runner-started.txt").exists())
            with (self.root / "maintenance.lock").open("r+b"):
                pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_multiple_readers_and_abrupt_release(self):
        processes = [
            subprocess.Popen(
                [str(HARNESS), "hold", str(self.root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            self.assertEqual(self.verify(), 0)
            with self.assertRaises(PermissionError):
                with (self.root / "maintenance.lock").open("r+b"):
                    pass
            for process in processes:
                process.kill()
                process.wait(timeout=5)
            with (self.root / "maintenance.lock").open("r+b"):
                pass
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
