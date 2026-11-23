"""Runner release e Job Object reais; nao usa SDK para abrir o bundle."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_supervisor import Harness, ProcessHandle, descendants
from test_windows_app import USER, capture, listening_backend, wait_for, window_for

BUNDLE = Path(sys.argv.pop(1)).resolve()


class WindowsBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edisk-release-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_packaged_backend_job_normal_and_abrupt(self):
        for abrupt in (False, True):
            (self.root / str(abrupt)).mkdir()
            with Harness(
                self.root / str(abrupt), backend=BUNDLE / "backend/edisk-backend.exe"
            ) as harness:
                ready = harness.wait("ready")
                self.assertGreater(ready["port"], 0)
                handles = [ProcessHandle(pid) for pid in descendants(harness.process.pid)]
                try:
                    if abrupt:
                        harness.process.kill()
                        harness.process.wait(timeout=5)
                    else:
                        self.assertEqual(harness.stop()["active"], 0)
                    # O fim do supervisor nao confirma sozinho o fim dos filhos.
                    # Exigir contencao no prazo existente, sem aceitar sobreviventes.
                    wait_for(
                        lambda handles=handles: all(handle.dead() for handle in handles), timeout=5
                    )
                    self.assertTrue(all(handle.dead() for handle in handles))
                finally:
                    for handle in handles:
                        handle.close()

    def test_release_readonly_unicode_other_cwd_and_poisoned_environment(self):
        copy = self.root / "pasta com espaços e ação"
        shutil.copytree(BUNDLE, copy)
        identity = (
            subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
            .strip()
            .split(",")[-1]
            .strip('"')
        )
        subprocess.run(
            ["icacls", str(copy), "/inheritance:r", "/grant:r", "*" + identity + ":(OI)(CI)(RX)"],
            capture_output=True,
            check=True,
        )
        try:
            self.assertGreater(len((copy / "edisk.exe").read_bytes()), 0)
            with self.assertRaises(PermissionError):
                (copy / "escrita-proibida.tmp").write_bytes(b"nao permitido")
            for abrupt in (False, True):
                process = subprocess.Popen(
                    [
                        str(copy / "edisk.exe"),
                        "--edisk-test-root=" + str(self.root / "dados externos"),
                    ],
                    cwd=self.root,
                    env={
                        **os.environ,
                        "EDISK_DEV_ROOT": "Z:/inexistente",
                        "EDISK_ENV": "production",
                        "EDISK_TEST_ROOT": "Z:/inexistente",
                        "PYTHONPATH": "Z:/inexistente",
                        "PATH": os.environ["SystemRoot"] + "/System32",
                    },
                )
                handles = []
                try:
                    hwnd, _ = wait_for(lambda process=process: window_for({process.pid}))
                    wait_for(lambda process=process: descendants(process.pid))
                    try:
                        # O bootloader pode admitir o interpretador depois do
                        # primeiro snapshot da árvore. Observar descendentes atuais.
                        wait_for(
                            lambda process=process: listening_backend(descendants(process.pid)),
                            timeout=20,
                        )
                    except AssertionError:
                        capture(hwnd, "entrega9-bundle-rx-diagnostico.png")
                        raise
                    handles = [ProcessHandle(pid) for pid in descendants(process.pid)]
                    time.sleep(1)
                    capture(hwnd, "entrega7-readonly-ready.png")
                    if abrupt:
                        process.kill()
                    else:
                        USER.PostMessageW(hwnd, 0x0010, 0, 0)
                    process.wait(timeout=16)
                    self.assertTrue(all(handle.dead() for handle in handles))
                    self.assertTrue((self.root / "dados externos/data/edisk.sqlite3").is_file())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    for handle in handles:
                        handle.close()
        finally:
            subprocess.run(
                [
                    "icacls",
                    str(copy),
                    "/grant:r",
                    "*" + identity + ":(OI)(CI)(F)",
                    "/inheritance:e",
                ],
                capture_output=True,
                check=True,
            )

    def test_missing_backend_window_stays_closable(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                copy = self.root / ("backend-invalido" if corrupt else "sem-backend")
                shutil.copytree(BUNDLE, copy)
                backend = copy / "backend/edisk-backend.exe"
                if corrupt:
                    backend.write_bytes(b"executavel invalido")
                else:
                    backend.unlink()
                process = subprocess.Popen(
                    [str(copy / "edisk.exe"), "--edisk-test-root=" + str(self.root / "dados")],
                    cwd=self.root,
                )
                try:
                    hwnd, _ = wait_for(lambda process=process: window_for({process.pid}))
                    time.sleep(1)
                    self.assertFalse(descendants(process.pid))
                    USER.PostMessageW(hwnd, 0x0010, 0, 0)
                    self.assertEqual(process.wait(timeout=10), 0)
                    self.assertFalse((self.root / "dados").exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
