"""Integração Windows com o supervisor C++ real; nenhum segredo em saída de teste."""

import ctypes
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path

from app.persistencia.instance_lock import InstanceLock

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "frontend/build/supervisor-tests/Debug/supervisor_harness.exe"
FAKE = Path(__file__).with_name("fake_backend.py")
PYTHON = ROOT / "backend/.venv/Scripts/python.exe"
KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL.OpenProcess.restype = wintypes.HANDLE
KERNEL.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
KERNEL.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
KERNEL.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD),
        ("heap", ctypes.c_size_t),
        ("module", wintypes.DWORD),
        ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
        ("exe", wintypes.WCHAR * 260),
    ]


KERNEL.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
KERNEL.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]


def descendants(pid):
    handle = KERNEL.CreateToolhelp32Snapshot(2, 0)
    assert handle != ctypes.c_void_p(-1).value
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        present = KERNEL.Process32FirstW(handle, ctypes.byref(entry))
        while present:
            parents[entry.pid] = entry.parent
            present = KERNEL.Process32NextW(handle, ctypes.byref(entry))
    finally:
        KERNEL.CloseHandle(handle)
    found = {pid}
    while True:
        extra = {child for child, parent in parents.items() if parent in found}
        if extra <= found:
            return found - {pid}
        found |= extra


class ProcessHandle:
    def __init__(self, pid):
        self.value = KERNEL.OpenProcess(0x100001, False, pid)
        assert self.value, "Processo de teste não pôde ser aberto"

    def dead(self):
        return KERNEL.WaitForSingleObject(self.value, 5000) == 0

    def kill(self):
        assert KERNEL.TerminateProcess(self.value, 1)

    def close(self):
        KERNEL.CloseHandle(self.value)


class Harness:
    def __init__(
        self,
        root,
        *,
        fake=None,
        mode="normal",
        executable=HARNESS,
        extra=(),
        backend=None,
        script=None,
    ):
        self.events = queue.Queue()
        args = [str(PYTHON), str(root), "-u", "-B"]
        args += [str(script or FAKE), fake, *extra] if fake else ["-m", "app"]
        if backend is not None:
            args = [str(backend), str(root)]
        self.process = subprocess.Popen(
            [str(executable), mode, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "EDISK_ENV": "test", "EDISK_TEST_ROOT": str(root / "data root")},
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.process.stdout:
            parts = line.split()
            self.events.put(
                dict(
                    state=parts[0],
                    seq=int(parts[1]),
                    launcher=int(parts[2]),
                    backend=int(parts[3]),
                    port=int(parts[4]),
                    code=parts[5],
                    active=int(parts[6]),
                    forced=parts[7] == "1",
                )
            )
        self.events.put(None)

    def wait(self, *states, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            assert event is not None, "Harness encerrou antes do estado esperado"
            if event["state"] in states:
                return event
            assert event["state"] not in {"failed", "exited", "stopped"}, event

    def stop(self):
        self.process.stdin.write("stop\n")
        self.process.stdin.flush()
        event = self.wait("stopped", "failed")
        assert self.process.wait(timeout=5) == 0
        assert event["active"] == 0, event
        return event

    def close(self):
        if self.process.poll() is None:
            self.process.kill()  # Job fechado pelo Windows, inclusive em falha do teste.
            self.process.wait(timeout=5)
        self.thread.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="edisk supervisor espaços ")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_real_backend_normal_and_lock_reopen(self):
        for mode in ("normal", "nested"):
            with self.subTest(mode=mode), Harness(self.root, mode=mode) as harness:
                ready = harness.wait("ready")
                self.assertGreater(ready["port"], 0)
                backend = ProcessHandle(ready["backend"])
                launcher = ProcessHandle(ready["launcher"])
                try:
                    result = harness.stop()
                    self.assertEqual(result["state"], "stopped")
                    self.assertFalse(result["forced"])
                    self.assertTrue(backend.dead())
                    self.assertTrue(launcher.dead())
                finally:
                    backend.close()
                    launcher.close()
            lock = InstanceLock(self.root / "data root/data/edisk.sqlite3")
            lock.acquire()
            lock.release()

    def test_invalid_protocol_is_contained(self):
        for mode in (
            "arbitrary",
            "oversized",
            "version",
            "truncated",
            "out_of_order",
            "wrong_identity",
            "outside_job",
            "zero_pid",
            "duplicate",
        ):
            extra = (str(os.getpid()),) if mode == "outside_job" else ()
            with self.subTest(mode=mode), Harness(self.root, fake=mode, extra=extra) as harness:
                result = harness.wait("failed")
                self.assertIn(result["code"], {"protocol", "identity", "startup"})
                self.assertEqual(result["active"], 0)

    def test_backend_crash_detected_and_tree_closed(self):
        with Harness(self.root) as harness:
            ready = harness.wait("ready")
            child = ProcessHandle(ready["backend"])
            launcher = ProcessHandle(ready["launcher"])
            try:
                child.kill()
                result = harness.wait("exited")
                self.assertEqual(result["active"], 0)
                self.assertTrue(child.dead())
                self.assertTrue(launcher.dead())
            finally:
                child.close()
                launcher.close()

    def test_runner_crash_kills_children_and_grandchildren(self):
        with Harness(self.root, fake="tree") as harness:
            ready = harness.wait("ready")
            deadline = time.monotonic() + 10
            pids = set()
            while len(pids) < 2 and time.monotonic() < deadline:
                pids = descendants(ready["backend"])
                time.sleep(0.05)
            self.assertGreaterEqual(len(pids), 2)
            handles = [ProcessHandle(pid) for pid in pids | {ready["backend"], ready["launcher"]}]
            try:
                harness.process.kill()
                harness.process.wait(timeout=5)
                self.assertTrue(all(handle.dead() for handle in handles))
            finally:
                for handle in handles:
                    handle.close()

    def test_forced_shutdown_kills_tree(self):
        with Harness(self.root, fake="ignore_stop") as harness:
            ready = harness.wait("ready")
            child = ProcessHandle(ready["backend"])
            try:
                result = harness.stop()
                self.assertEqual(result["state"], "stopped")
                self.assertTrue(result["forced"])
                self.assertTrue(child.dead())
            finally:
                child.close()

    def test_runner_crash_during_creation(self):
        for delay in (0, 0.001, 0.01, 0.05, 0.1, 0.2):
            with self.subTest(delay=delay), Harness(self.root) as harness:
                time.sleep(delay)
                pids = descendants(harness.process.pid)
                handles = []
                for pid in pids:
                    handle = KERNEL.OpenProcess(0x100000, False, pid)
                    if handle:
                        handles.append(handle)
                try:
                    harness.process.kill()
                    harness.process.wait(timeout=5)
                    self.assertTrue(
                        all(KERNEL.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
                    )
                    deadline = time.monotonic() + 5
                    remaining = descendants(harness.process.pid)
                    while remaining and time.monotonic() < deadline:
                        time.sleep(0.01)
                        remaining = descendants(harness.process.pid)
                    self.assertEqual(remaining, set())
                finally:
                    for handle in handles:
                        KERNEL.CloseHandle(handle)

    def test_incompatible_outer_job_fails_closed(self):
        with Harness(self.root, mode="restricted") as harness:
            result = harness.wait("failed")
            self.assertIn(result["code"], {"containment", "launch"})
            self.assertEqual(result["active"], 0)

    def test_handshake_timeout(self):
        with Harness(self.root, fake="no_hello") as harness:
            result = harness.wait("failed", timeout=12)
            self.assertEqual(result["code"], "handshake_timeout")
            self.assertEqual(result["active"], 0)

    def test_startup_deadline_not_extended_by_preparing(self):
        with Harness(self.root, fake="no_ready") as harness:
            result = harness.wait("failed", timeout=70)
            self.assertEqual(result["code"], "startup_timeout")
            self.assertEqual(result["active"], 0)

    def test_health_confirmation_has_total_deadline(self):
        with Harness(self.root, mode="unconfirmed") as harness:
            harness.wait("ready")
            result = harness.wait("failed", timeout=70)
            self.assertEqual(result["code"], "startup_timeout")
            self.assertEqual(result["active"], 0)

    def test_same_database_conflict_preserves_first(self):
        with Harness(self.root) as first:
            first.wait("ready")
            with Harness(self.root) as second:
                result = second.wait("failed")
                self.assertEqual(result["code"], "instance_in_use")
                self.assertEqual(result["active"], 0)
            self.assertFalse(first.stop()["forced"])

    def test_failure_before_ready_and_lock_release(self):
        data_root = self.root / "data root"
        data_root.mkdir()
        (data_root / "data").write_text("diretório bloqueado", encoding="utf-8")
        with Harness(self.root) as harness:
            result = harness.wait("failed")
            self.assertEqual(result["code"], "persistence")
            self.assertEqual(result["active"], 0)

    def test_paths_spaces_quotes_and_stderr_drain(self):
        executable = self.root / "caminho com espaços" / "supervisor.exe"
        executable.parent.mkdir()
        shutil.copyfile(HARNESS, executable)
        for mode in ("arguments", "stderr"):
            extra = ('espaços "aspas" final\\',) if mode == "arguments" else ()
            with (
                self.subTest(mode=mode),
                Harness(self.root, fake=mode, executable=executable, extra=extra) as harness,
            ):
                harness.wait("ready")
                self.assertFalse(harness.stop()["forced"])

    def test_stop_during_initialization(self):
        with Harness(self.root) as harness:
            result = harness.stop()
            self.assertEqual(result["state"], "stopped")
            self.assertFalse(result["forced"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
