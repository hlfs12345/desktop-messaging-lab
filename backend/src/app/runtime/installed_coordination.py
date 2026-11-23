"""Backend instalado participa do Job coordenado; nao aceita execucao avulsa."""

import ctypes
import os
import re
import sys
from pathlib import Path


def verify_installed_coordination() -> None:
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    executable = Path(sys.executable)
    release = executable.parent.parent
    if release.parent.name.casefold() != "releases":
        return
    identity = os.environ.get("EDISK_INSTALL_JOB", "")
    root = release.parent.parent
    if not re.fullmatch("[0-9a-f]{32}", identity):
        raise ValueError("Backend instalado exige coordenador.")
    if not (root / "runtime" / (identity + ".claim")).is_file():
        raise ValueError("Confirmacao de ciclo de vida ausente.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenJobObjectW.restype = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    job = kernel.OpenJobObjectW(4, False, "Local\\eDisk-" + identity)
    if not job:
        raise ValueError("Job instalado ausente.")
    try:
        member = ctypes.c_int()
        if (
            not kernel.IsProcessInJob(kernel.GetCurrentProcess(), job, ctypes.byref(member))
            or not member.value
        ):
            raise ValueError("Backend fora do Job instalado.")
    finally:
        kernel.CloseHandle(job)
