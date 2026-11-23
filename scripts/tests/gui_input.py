"""Entrada GUI direcionada somente à janela do processo de ensaio."""

import ctypes
import time
from ctypes import wintypes

USER = ctypes.WinDLL("user32", use_last_error=True)
USER.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def require(value, message):
    if not value:
        raise AssertionError(message)


def flutter_child(hwnd):
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    USER.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    USER.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    targets = []

    @callback
    def visit(child, _):
        name = ctypes.create_unicode_buffer(256)
        USER.GetClassNameW(child, name, 256)
        if name.value == "FLUTTERVIEW":
            targets.append(child)
        return True

    USER.EnumChildWindows(hwnd, visit, 0)
    require(len(targets) == 1, "Janela Flutter exclusiva nao encontrada.")
    target = targets[0]
    owner = wintypes.DWORD()
    child_owner = wintypes.DWORD()
    USER.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    USER.GetWindowThreadProcessId(target, ctypes.byref(child_owner))
    require(owner.value == child_owner.value, "Janela filha pertence a outro processo.")
    return target


def click_field(hwnd, logical_y, logical_x=None):
    target = flutter_child(hwnd)
    USER.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    USER.GetDpiForWindow.argtypes = [wintypes.HWND]
    USER.GetDpiForWindow.restype = wintypes.UINT
    rect = wintypes.RECT()
    require(USER.GetClientRect(target, ctypes.byref(rect)), "Retangulo Flutter indisponivel.")
    scale = USER.GetDpiForWindow(target) / 96
    x = (
        (rect.right - rect.left) // 2
        if logical_x is None
        else (rect.right + round(logical_x * scale) if logical_x < 0 else round(logical_x * scale))
    )
    y = round(logical_y * scale)
    require(0 < x < rect.right and 0 < y < rect.bottom, "Campo fora da janela de ensaio.")
    point = x | (y << 16)
    for message, flags in ((0x200, 0), (0x201, 1), (0x202, 0)):
        require(USER.PostMessageW(target, message, flags, point), "Clique de ensaio nao enviado.")
    time.sleep(0.25)


def keys(hwnd, text=None, virtual_key=13):
    target = flutter_child(hwnd)
    if text is None:
        scan = 0x1C if virtual_key == 13 else 0x0F
        require(
            USER.PostMessageW(target, 0x100, virtual_key, 1 | (scan << 16)), "Tecla nao enviada."
        )
        if virtual_key == 13:
            require(USER.PostMessageW(target, 0x102, 13, 1 | (scan << 16)), "Acao nao enviada.")
        require(
            USER.PostMessageW(target, 0x101, virtual_key, 1 | (scan << 16) | (3 << 30)),
            "Fim de tecla nao enviado.",
        )
    else:
        for char in text:
            require(USER.PostMessageW(target, 0x102, ord(char), 1), "Texto nao enviado.")
    time.sleep(0.4)
