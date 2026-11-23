"""AES-256-GCM nativo Windows; sem algoritmo criptografico implementado em Python."""

import ctypes as C
import os
from functools import lru_cache

from app.contatos.errors import NegocioError


def indisponivel():
    return NegocioError(
        "conteudo_indisponivel",
        "Chave incompativel, protecao indisponivel ou dados danificados.",
        503,
    )


class AuthInfo(C.Structure):
    _fields_ = [
        ("size", C.c_ulong),
        ("version", C.c_ulong),
        ("nonce", C.c_void_p),
        ("nonce_size", C.c_ulong),
        ("aad", C.c_void_p),
        ("aad_size", C.c_ulong),
        ("tag", C.c_void_p),
        ("tag_size", C.c_ulong),
        ("mac", C.c_void_p),
        ("mac_size", C.c_ulong),
        ("aad_count", C.c_ulong),
        ("data_count", C.c_ulonglong),
        ("flags", C.c_ulong),
    ]


@lru_cache(maxsize=1)
def api():
    if os.name != "nt":
        raise indisponivel()
    lib = C.WinDLL("bcrypt", use_last_error=True)
    signatures = {
        "BCryptOpenAlgorithmProvider": [C.c_void_p, C.c_wchar_p, C.c_wchar_p, C.c_ulong],
        "BCryptSetProperty": [C.c_void_p, C.c_wchar_p, C.c_void_p, C.c_ulong, C.c_ulong],
        "BCryptGetProperty": [
            C.c_void_p,
            C.c_wchar_p,
            C.c_void_p,
            C.c_ulong,
            C.c_void_p,
            C.c_ulong,
        ],
        "BCryptGenerateSymmetricKey": [
            C.c_void_p,
            C.c_void_p,
            C.c_void_p,
            C.c_ulong,
            C.c_void_p,
            C.c_ulong,
            C.c_ulong,
        ],
        "BCryptDestroyKey": [C.c_void_p],
        "BCryptCloseAlgorithmProvider": [C.c_void_p, C.c_ulong],
    }
    for name in ("BCryptEncrypt", "BCryptDecrypt"):
        signatures[name] = [
            C.c_void_p,
            C.c_void_p,
            C.c_ulong,
            C.c_void_p,
            C.c_void_p,
            C.c_ulong,
            C.c_void_p,
            C.c_ulong,
            C.c_void_p,
            C.c_ulong,
        ]
    for name, args in signatures.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, C.c_long
    return lib


def check(status):
    if status != 0:
        raise indisponivel()


class GCM:
    """Um handle por chave/operacao; nao compartilhar entre threads."""

    def __init__(self, key):
        if len(key) != 32:
            raise indisponivel()
        self.lib = api()
        self.alg, self.key = C.c_void_p(), C.c_void_p()
        self._fechado = False
        secret = C.create_string_buffer(key)
        try:
            check(
                self.lib.BCryptOpenAlgorithmProvider(
                    C.byref(self.alg), "AES", "Microsoft Primitive Provider", 0
                )
            )
            mode = C.create_unicode_buffer("ChainingModeGCM")
            check(self.lib.BCryptSetProperty(self.alg, "ChainingMode", mode, C.sizeof(mode), 0))
            # Windows 7+: CNG e dono do objeto e o libera em DestroyKey.
            # Nao liberar buffer Python enquanto um handle puder permanecer vivo.
            check(
                self.lib.BCryptGenerateSymmetricKey(
                    self.alg, C.byref(self.key), None, 0, secret, 32, 0
                )
            )
        except Exception:
            self.close(suppress_errors=True)
            raise
        finally:
            C.memset(secret, 0, C.sizeof(secret))

    @staticmethod
    def _liberar(fn, *args):
        failed = False
        for _ in range(2):
            try:
                status = fn(*args)
            except Exception:
                failed = True
                continue
            if status == 0:
                return True, failed
            failed = True
            if status & 0xFFFFFFFF == 0xC0000008:  # STATUS_INVALID_HANDLE: ja inexistente.
                return True, failed
        return False, True

    def close(self, *, suppress_errors=False):
        self._fechado = True
        failed = False
        if getattr(self, "key", None):
            released, error = self._liberar(self.lib.BCryptDestroyKey, self.key)
            failed |= error
            if released:
                self.key = C.c_void_p()
        # Mesmo se destruir a chave falhar, tentar fechar os outros recursos.
        if getattr(self, "alg", None):
            released, error = self._liberar(self.lib.BCryptCloseAlgorithmProvider, self.alg, 0)
            failed |= error
            if released:
                self.alg = C.c_void_p()
        if failed and not suppress_errors:
            raise indisponivel()

    def __del__(self):
        # Ultima tentativa somente dos handles que nao foram liberados.
        try:
            if getattr(self, "key", None) or getattr(self, "alg", None):
                self.close(suppress_errors=True)
        except Exception:
            pass

    def __enter__(self):
        if self._fechado:
            raise indisponivel()
        return self

    def __exit__(self, exc_type, _exc, _tb):
        self.close(suppress_errors=exc_type is not None)

    def _run(self, nonce, data, aad, decrypt):
        if self._fechado or not self.key or len(nonce) != 12 or (decrypt and len(data) < 16):
            raise indisponivel()
        payload, tag = (data[:-16], data[-16:]) if decrypt else (data, bytes(16))
        source = C.create_string_buffer(payload)
        target = C.create_string_buffer(max(1, len(payload)))
        nonce_buf, aad_buf, tag_buf = (C.create_string_buffer(x) for x in (nonce, aad, tag))
        info = AuthInfo()
        info.size, info.version = C.sizeof(info), 1
        info.nonce, info.nonce_size = C.cast(nonce_buf, C.c_void_p), 12
        info.aad, info.aad_size = C.cast(aad_buf, C.c_void_p), len(aad)
        info.tag, info.tag_size = C.cast(tag_buf, C.c_void_p), 16
        used = C.c_ulong()
        try:
            fn = self.lib.BCryptDecrypt if decrypt else self.lib.BCryptEncrypt
            check(
                fn(
                    self.key,
                    source,
                    len(payload),
                    C.byref(info),
                    None,
                    0,
                    target,
                    len(target),
                    C.byref(used),
                    0,
                )
            )
            if used.value != len(payload):
                raise indisponivel()
            result = target.raw[: used.value]
            return result if decrypt else result + tag_buf.raw[:16]
        finally:
            C.memset(source, 0, C.sizeof(source))
            C.memset(target, 0, C.sizeof(target))

    def encrypt(self, nonce, data, aad):
        return self._run(nonce, data, aad, False)

    def decrypt(self, nonce, data, aad):
        return self._run(nonce, data, aad, True)
