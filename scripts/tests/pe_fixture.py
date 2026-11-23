"""Recurso de variante para fixtures PE exclusivas dos ensaios."""

import ctypes
import struct


def variant(path, version, channel=1, profile=1):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    kernel.BeginUpdateResourceW.restype = ctypes.c_void_p
    kernel.UpdateResourceW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ushort,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    kernel.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    handle = kernel.BeginUpdateResourceW(str(path), False)
    assert handle
    data = struct.pack(
        "<8sIII6H", b"EDSKVAR2", 2, channel, profile, *map(int, version.split(".")), 0x8664, 3
    )
    assert kernel.UpdateResourceW(handle, 10, 201, 0, ctypes.create_string_buffer(data), len(data))
    assert kernel.EndUpdateResourceW(handle, False)
