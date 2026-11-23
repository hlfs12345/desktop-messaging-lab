"""Ensaios reais do runner e do tooling Flutter, sem persistir tokens ou transcrições."""

import ctypes
import json
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
import zlib
from ctypes import wintypes
from pathlib import Path

from gui_input import click_field, keys
from test_supervisor import KERNEL, ROOT, ProcessHandle, descendants

from app.persistencia.instance_lock import InstanceLock

USER = ctypes.WinDLL("user32", use_last_error=True)
GDI = ctypes.WinDLL("gdi32", use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
USER.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
USER.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER.IsWindowVisible.argtypes = [wintypes.HWND]
USER.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER.GetWindowDC.argtypes = [wintypes.HWND]
USER.GetWindowDC.restype = wintypes.HDC
USER.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
GDI.CreateCompatibleDC.argtypes = [wintypes.HDC]
GDI.CreateCompatibleDC.restype = wintypes.HDC
GDI.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
GDI.CreateCompatibleBitmap.restype = wintypes.HBITMAP
GDI.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
GDI.SelectObject.restype = wintypes.HANDLE
GDI.DeleteObject.argtypes = [wintypes.HANDLE]
GDI.DeleteDC.argtypes = [wintypes.HDC]
GDI.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
GDI.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
CAPTURES = ROOT / "frontend/build/validation"


def listening_backend(pids):
    # Identificar pelo socket HTTP efetivo, não pela posição na árvore. O Windows
    # pode criar auxiliares de console/renderização que também são folhas.
    helper = ctypes.WinDLL("iphlpapi", use_last_error=True)
    helper.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    size = wintypes.DWORD()
    helper.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
    assert 0 < size.value < 1024 * 1024
    table = ctypes.create_string_buffer(size.value)
    assert helper.GetExtendedTcpTable(table, ctypes.byref(size), False, 2, 3, 0) == 0
    data = table.raw
    count = struct.unpack_from("<I", data)[0]
    owners = []
    for index in range(count):
        state, address, _port, _remote, _remote_port, pid = struct.unpack_from(
            "<6I", data, 4 + index * 24
        )
        if state == 2 and address == 0x0100007F and pid in pids:
            owners.append(pid)
    return owners[0] if len(owners) == 1 else None


def window_for(pids):
    found = []

    @CALLBACK
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        USER.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and USER.IsWindowVisible(hwnd):
            found.append((hwnd, pid.value))
        return True

    USER.EnumWindows(visit, 0)
    return found[0] if found else None


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Condição do ensaio Windows não foi satisfeita no prazo")


def capture(hwnd, name):
    rect = wintypes.RECT()
    USER.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    source = USER.GetWindowDC(hwnd)
    target = GDI.CreateCompatibleDC(source)
    bitmap = GDI.CreateCompatibleBitmap(source, width, height)
    previous = GDI.SelectObject(target, bitmap)
    try:
        assert GDI.BitBlt(target, 0, 0, width, height, source, 0, 0, 0x00CC0020)
        info = struct.pack(
            "<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0
        )
        pixels = ctypes.create_string_buffer(width * height * 4)
        assert GDI.GetDIBits(target, bitmap, 0, height, pixels, info, 0) == height
        raw = bytearray()
        data = pixels.raw
        for row in range(height):
            raw.append(0)
            for col in range(width):
                index = (row * width + col) * 4
                raw.extend((data[index + 2], data[index + 1], data[index]))

        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload))
            )

        png = b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        )
        png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        CAPTURES.mkdir(parents=True, exist_ok=True)
        (CAPTURES / name).write_bytes(png)
    finally:
        GDI.SelectObject(target, previous)
        GDI.DeleteObject(bitmap)
        GDI.DeleteDC(target)
        USER.ReleaseDC(hwnd, source)


class FlutterTool:
    def __init__(self, root):
        sdk = Path(shutil.which("flutter")).parent.parent
        self.events = queue.Queue()
        self.responses = {}
        self.app_id = None
        self.vm_uri = None
        self.last_state = None
        self.process = subprocess.Popen(
            [
                str(sdk / "bin/cache/dart-sdk/bin/dart.exe"),
                str(sdk / "bin/cache/flutter_tools.snapshot"),
                "run",
                "--machine",
                "-d",
                "windows",
                "--debug",
                "--no-pub",
                "--no-dds",
                "--no-devtools",
            ],
            cwd=ROOT / "frontend",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "EDISK_DEV_ROOT": str(ROOT),
                "EDISK_ENV": "test",
                "EDISK_TEST_ROOT": str(root),
            },
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            wait_for(lambda: self.app_id and self.vm_uri, timeout=90)
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            # O tooling pode emitir URLs com credencial de depuração: descartar
            # saída arbitrária e nunca salvar/imprimir a transcrição.
            try:
                messages = json.loads(line)
            except ValueError, TypeError:
                continue
            if not isinstance(messages, list):
                continue
            for message in messages:
                if message.get("event") == "app.start":
                    self.app_id = message["params"]["appId"]
                elif message.get("event") == "app.debugPort":
                    self.vm_uri = message["params"]["wsUri"]
                elif "id" in message:
                    self.responses[message["id"]] = message

    def command(self, method, **params):
        identifier = len(self.responses) + 100
        request = [{"id": identifier, "method": method, "params": {"appId": self.app_id, **params}}]
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        result = wait_for(lambda: self.responses.get(identifier), timeout=60)
        assert "error" not in result, "Comando do tooling recusado"
        return result.get("result")

    def vm(self, method, **params):
        uri = urllib.parse.urlsplit(self.vm_uri)
        path = uri.path.removesuffix("/ws") + "/" + method
        url = urllib.parse.urlunsplit(
            ("http", uri.netloc, path, urllib.parse.urlencode(params), "")
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            result = json.load(response)
        assert "error" not in result, "Consulta de depuração recusada"
        return result.get("result", result)

    def controller_state(self):
        try:
            isolates = self.vm("getVM")["isolates"]
            isolate = next(item for item in isolates if item["name"] == "main")["id"]
            classes = self.vm("getClassList", isolateId=isolate)["classes"]
            controller = next(item for item in classes if item["name"] == "ControladorConexao")
            instances = self.vm(
                "getInstances", isolateId=isolate, objectId=controller["id"], limit=20
            )
            for instance in instances["instances"]:
                result = self.vm(
                    "evaluate", isolateId=isolate, targetId=instance["id"], expression="estado.name"
                )
                if result.get("valueAsString") in {
                    "disponivel",
                    "backendEncerrado",
                    "falhaInicializacao",
                }:
                    self.last_state = result["valueAsString"]
                    if self.last_state == "falhaInicializacao":
                        code = self.vm(
                            "evaluate",
                            isolateId=isolate,
                            targetId=instance["id"],
                            expression="codigo",
                        )
                        self.last_state += ":" + code.get("valueAsString", "unknown")
                    return result["valueAsString"]
        except Exception, StopIteration:
            return None
        return None

    def close(self):
        # O tooling não é o supervisor do produto. Encerrar a janela conhecida
        # mesmo quando o ensaio falhar antes de receber appId ou VM service.
        window = window_for(descendants(self.process.pid))
        if window:
            USER.PostMessageW(window[0], 0x0010, 0, 0)
        if self.process.poll() is None:
            try:
                self.command("app.stop")
                self.process.wait(timeout=15)
            except Exception:
                window = window_for(descendants(self.process.pid))
                if window:
                    runner = ProcessHandle(window[1])
                    try:
                        runner.kill()
                        assert runner.dead()
                    finally:
                        runner.close()
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()

    def auth_command(self, expression):
        """Estímulos somente em debug, sem extrair tokens ou registrar URLs VM."""
        isolates = self.vm("getVM")["isolates"]
        isolate = next(item for item in isolates if item["name"] == "main")["id"]
        classes = self.vm("getClassList", isolateId=isolate)["classes"]
        controller = next(item for item in classes if item["name"] == "ControladorAutenticacao")
        instances = self.vm("getInstances", isolateId=isolate, objectId=controller["id"], limit=20)
        for instance in instances["instances"]:
            state = self.vm(
                "evaluate", isolateId=isolate, targetId=instance["id"], expression="estado.name"
            )
            if state.get("valueAsString") != "desconectado":
                return self.vm(
                    "evaluate", isolateId=isolate, targetId=instance["id"], expression=expression
                )
        return {}

    def auth_state(self):
        try:
            # evaluate retorna o Future antes de seu finally. O estado pode já
            # estar publicado enquanto a ação seguinte ainda está bloqueada.
            # Assim como os botões, só operar quando ocupado estiver falso.
            return self.auth_command("ocupado ? '' : estado.name").get("valueAsString")
        except Exception:
            return None

    def gui_bootstrap(self, hwnd):
        time.sleep(0.5)
        for y, value in zip(
            (255, 320, 405, 490),
            (
                "Pessoa Ficticia",
                "teste.local",
                "frase ficticia para ensaio Windows 493!",
                "frase ficticia para ensaio Windows 493!",
            ),
            strict=True,
        ):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)

    def gui_login(self, hwnd):
        time.sleep(0.5)
        for y, value in zip(
            (225, 310), ("teste.local", "frase ficticia para ensaio Windows 493!"), strict=True
        ):
            click_field(hwnd, y)
            keys(hwnd, value)
        keys(hwnd)

    def gui_logout(self, hwnd):
        click_field(hwnd, 28, logical_x=-35)

    def wait_auth(self, expected):
        try:
            wait_for(lambda: self.auth_state() == expected)
        except AssertionError:
            isolate = next(i for i in self.vm("getVM")["isolates"] if i["name"] == "main")["id"]
            details = self.vm("getIsolate", isolateId=isolate)
            frames = self.vm("getStack", isolateId=isolate).get("frames", [])
            diagnostic = {
                "esperado": expected,
                "estado": self.auth_command("estado.name").get("valueAsString"),
                "ocupado": self.auth_command("ocupado.toString()").get("valueAsString"),
                "mensagem": self.auth_command("mensagem ?? ''").get("valueAsString"),
                "pausa": details.get("pauseEvent", {}).get("kind"),
                "funcoes": [f.get("function", {}).get("name") for f in frames],
            }
            raise AssertionError(
                "Diagnóstico público: " + json.dumps(diagnostic, ensure_ascii=True)
            ) from None


class WindowsAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edisk janela real ")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_close_with_native_file_dialog_open(self):
        tool = FlutterTool(self.root)
        handles = []
        USER.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        try:
            hwnd, runner_pid = wait_for(lambda: window_for(descendants(tool.process.pid)))
            wait_for(lambda: tool.controller_state() == "disponivel")
            handles = [ProcessHandle(pid) for pid in [runner_pid, *descendants(runner_pid)]]
            isolate = next(i for i in tool.vm("getVM")["isolates"] if i["name"] == "main")["id"]
            classes = tool.vm("getClassList", isolateId=isolate)["classes"]
            target = next(c for c in classes if c["name"] == "ArquivosLocais")
            library = tool.vm("getObject", isolateId=isolate, objectId=target["id"])["library"][
                "id"
            ]
            tool.vm(
                "evaluate",
                isolateId=isolate,
                targetId=library,
                expression="ArquivosLocais().abrir()",
            )

            def dialog():
                found = []

                @CALLBACK
                def visit(window, _):
                    pid = wintypes.DWORD()
                    USER.GetWindowThreadProcessId(window, ctypes.byref(pid))
                    name = ctypes.create_unicode_buffer(128)
                    USER.GetClassNameW(window, name, 128)
                    if (
                        pid.value == runner_pid
                        and name.value == "#32770"
                        and USER.IsWindowVisible(window)
                    ):
                        found.append(window)
                    return True

                USER.EnumWindows(visit, 0)
                return found[0] if found else None

            wait_for(dialog)
            # Solicita fechar a janela dona enquanto GetOpenFileNameW está ativo.
            USER.PostMessageW(hwnd, 0x0010, 0, 0)
            wait_for(lambda: all(handle.dead() for handle in handles), timeout=20)
            self.assertFalse(dialog())
            lock = InstanceLock(self.root / "data/edisk.sqlite3")
            lock.acquire()
            lock.release()
        finally:
            tool.close()
            for handle in handles:
                handle.close()

    def test_debug_hot_reload_restart_and_backend_death(self):
        tool = FlutterTool(self.root)
        handles = []
        try:
            hwnd, runner_pid = wait_for(lambda: window_for(descendants(tool.process.pid)))
            wait_for(lambda: tool.controller_state() == "disponivel")
            wait_for(lambda: tool.auth_state() == "configurar")
            tool.gui_bootstrap(hwnd)
            try:
                tool.wait_auth("login")
            except AssertionError:
                capture(hwnd, "entrega9-bootstrap-diagnostico.png")
                # Apenas estado/mensagem pública sanitizada, nunca credenciais.
                state = tool.auth_command("estado.name").get("valueAsString")
                busy = tool.auth_command("ocupado.toString()").get("valueAsString")
                message = tool.auth_command("mensagem ?? ''").get("valueAsString")
                self.fail(
                    f"Bootstrap não concluiu: estado={state}, ocupado={busy}, mensagem={message}"
                )
            capture(hwnd, "auth-login.png")
            tool.gui_login(hwnd)
            tool.wait_auth("autenticado")
            capture(hwnd, "auth-authenticated.png")
            original = descendants(runner_pid)
            self.assertGreaterEqual(len(original), 1)
            handles = [ProcessHandle(pid) for pid in original]
            capture(hwnd, "debug-ready.png")
            for full in (False, True):
                result = tool.command("app.restart", fullRestart=full, pause=False, debounce=False)
                self.assertEqual(result["code"], 0)
                wait_for(lambda: tool.controller_state() == "disponivel")
                wait_for(
                    lambda full=full: tool.auth_state() == ("login" if full else "autenticado")
                )
                if full:
                    tool.gui_login(hwnd)
                    tool.wait_auth("autenticado")
                self.assertEqual(descendants(runner_pid), original)
                self.assertTrue(
                    all(KERNEL.WaitForSingleObject(handle.value, 0) == 258 for handle in handles)
                )
            capture(hwnd, "debug-after-hot-restart.png")
            tool.gui_logout(hwnd)
            tool.wait_auth("login")
            backend_pid = wait_for(lambda: listening_backend(original))
            backend = ProcessHandle(backend_pid)
            try:
                backend.kill()
                try:
                    wait_for(lambda: tool.controller_state() == "backendEncerrado")
                except AssertionError:
                    capture(hwnd, "debug-crash-diagnostic.png")
                    self.fail("Estado após queda: " + str(tool.last_state))
                capture(hwnd, "debug-backend-exited.png")
                self.assertTrue(all(handle.dead() for handle in handles))
            finally:
                backend.close()
            USER.PostMessageW(hwnd, 0x0010, 0, 0)
            wait_for(lambda: not window_for({runner_pid}))
        finally:
            tool.close()
            for handle in handles:
                handle.close()

    def test_authentication_reopen_requires_login_with_same_database(self):
        for first in (True, False):
            tool = FlutterTool(self.root)
            handles = []
            try:
                hwnd, runner_pid = wait_for(
                    lambda tool=tool: window_for(descendants(tool.process.pid))
                )
                wait_for(lambda tool=tool: tool.controller_state() == "disponivel")
                wait_for(
                    lambda tool=tool, first=first: (
                        tool.auth_state() == ("configurar" if first else "login")
                    )
                )
                handles = [ProcessHandle(pid) for pid in descendants(runner_pid)]
                if first:
                    tool.gui_bootstrap(hwnd)
                    tool.wait_auth("login")
                tool.gui_login(hwnd)
                tool.wait_auth("autenticado")
                capture(hwnd, "auth-first-open.png" if first else "auth-reopened.png")
                USER.PostMessageW(hwnd, 0x0010, 0, 0)
                wait_for(lambda runner_pid=runner_pid: not window_for({runner_pid}))
                self.assertTrue(all(handle.dead() for handle in handles))
            finally:
                tool.close()
                for handle in handles:
                    handle.close()

    def test_standalone_debug_and_release_normal_and_abrupt(self):
        for build in ("Debug", "Release"):
            for abrupt in (False, True):
                with self.subTest(build=build, abrupt=abrupt):
                    executable = ROOT / f"frontend/build/windows/x64/runner/{build}/edisk.exe"
                    process = subprocess.Popen(
                        [str(executable)],
                        cwd=self.root,
                        env={
                            **os.environ,
                            "EDISK_DEV_ROOT": str(ROOT),
                            "EDISK_ENV": "test",
                            "EDISK_TEST_ROOT": str(self.root / build),
                        },
                    )
                    handles = []
                    try:
                        hwnd, _ = wait_for(lambda process=process: window_for({process.pid}))
                        pids = wait_for(lambda process=process: descendants(process.pid))
                        time.sleep(3)
                        pids = descendants(process.pid)
                        handles = [ProcessHandle(pid) for pid in pids]
                        self.assertTrue(handles)
                        capture(
                            hwnd, f"{build.lower()}-{'abrupt' if abrupt else 'normal'}-ready.png"
                        )
                        if abrupt:
                            process.kill()
                        else:
                            USER.PostMessageW(hwnd, 0x0010, 0, 0)
                        self.assertEqual(process.wait(timeout=16), 1 if abrupt else 0)
                        self.assertTrue(all(handle.dead() for handle in handles))
                        lock = InstanceLock(self.root / build / "data/edisk.sqlite3")
                        lock.acquire()
                        lock.release()
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        for handle in handles:
                            handle.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
