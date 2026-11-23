"""DPAPI por usuário: somente ciphertext em disco, separado do banco de negócio."""

import ctypes
import hashlib
import hmac
import json
import os
import re
import stat
from uuid import UUID, uuid4

from app.contatos.errors import NegocioError

HEX = re.compile(r"[0-9a-f]{32}")
MAX_BLOB = 65536


def indisponivel():
    return NegocioError(
        "segredo_indisponivel",
        "Armazenamento protegido indisponível ou credencial danificada. Reconfigure a credencial.",
        503,
    )


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, *, decrypt=False):
    """Sem flag LOCAL_MACHINE, sem prompts, sem diagnóstico contendo entrada."""
    if os.name != "nt" or not 0 < len(data) <= MAX_BLOB:
        raise indisponivel()
    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    try:
        library = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        raise indisponivel() from None
    protect = library.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_wchar_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    protect.restype = ctypes.c_int
    unprotect = library.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    unprotect.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if decrypt:
            ok = unprotect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        else:
            ok = protect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
        if not ok:
            raise indisponivel()
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            kernel.LocalFree(target.data)


class SegredosWindows:
    def __init__(self, settings):
        self.root = settings.root / "secrets" / "contas-v1"
        self.contexto = {
            "ambiente": settings.environment,
            "raiz": str(settings.root).casefold(),
            "formato": 1,
        }

    def _guard(self, path):
        try:
            self._guard_path(path)
        except OSError:
            raise indisponivel() from None

    def _guard_path(self, path):
        if not path.is_relative_to(self.root) and path != self.root:
            raise indisponivel()
        for item in [*reversed(path.parents), path]:
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise indisponivel()
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise indisponivel()

    def _read(self, path):
        try:
            self._guard(path)
            with path.open("rb") as stream:
                data = stream.read(MAX_BLOB + 1)
            if not data or len(data) > MAX_BLOB:
                raise ValueError
            return data
        except OSError, ValueError:
            raise indisponivel() from None

    def _publish(self, partial, final):
        # Não sobrescreve gerações existentes; publicação local com write-through.
        if os.name != "nt":
            raise indisponivel()
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        move.restype = ctypes.c_int
        if not move(str(partial), str(final), 8):
            raise indisponivel()

    def _write(self, path, envelope):
        try:
            self._guard(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._guard(path)
            partial = path.with_suffix(".partial")
            self._guard(partial)
            # Pode restar de uma queda anterior à publicação; nunca contém plaintext.
            if partial.exists():
                partial.unlink()
            encrypted = dpapi(json.dumps(envelope, sort_keys=True).encode("utf-8"))
            with partial.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            self._guard(path)
            self._publish(partial, path)
        except OSError, ValueError:
            raise indisponivel() from None

    def _envelope(self, purpose, **data):
        return {**self.contexto, "finalidade": purpose, **data}

    def _decode(self, path, expected):
        try:
            result = json.loads(dpapi(self._read(path), decrypt=True))
            if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
                raise ValueError
            return result
        except ValueError, TypeError, UnicodeError:
            raise indisponivel() from None

    def _path(self, conta, generation):
        try:
            if str(UUID(conta)) != conta or not HEX.fullmatch(generation):
                raise ValueError
        except ValueError, TypeError:
            raise indisponivel() from None
        return self.root / UUID(conta).hex / (generation + ".dpapi")

    def assinatura(self, raw, secret):
        path = self.root / "key.dpapi"
        self._guard(path)
        if not path.exists():
            self._write(path, self._envelope("idempotencia", chave=os.urandom(32).hex()))
        envelope = self._decode(path, self._envelope("idempotencia"))
        try:
            key = bytes.fromhex(envelope["chave"])
            if len(key) != 32:
                raise ValueError
        except KeyError, ValueError, TypeError:
            raise indisponivel() from None
        # Separação inequívoca entre metadados canônicos e segredo.
        return hmac.new(key, raw + b"\x00" + secret.encode("utf-8"), hashlib.sha256).hexdigest()

    def gravar(self, conta, provedor, secret):
        generation = uuid4().hex
        self._write(
            self._path(conta, generation),
            self._envelope(
                "credencial", conta_id=conta, provedor=provedor, geracao=generation, valor=secret
            ),
        )
        return generation

    def recuperar(self, conta, provedor, generation):
        envelope = self._decode(
            self._path(conta, generation),
            self._envelope("credencial", conta_id=conta, provedor=provedor, geracao=generation),
        )
        value = envelope.get("valor")
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 4096
            or any(not 33 <= ord(c) <= 126 for c in value)
        ):
            raise indisponivel()
        return value

    def _orfaos(self, referencias):
        # A consulta e a coleta usam os mesmos nomes controlados, sob o lock do serviço.
        self._guard(self.root)
        if not self.root.exists():
            return
        key_partial = self.root / "key.partial"
        self._guard(key_partial)
        if key_partial.exists():
            yield key_partial
        for directory in self.root.iterdir():
            if not HEX.fullmatch(directory.name):
                continue
            self._guard(directory)
            if not directory.is_dir():
                raise indisponivel()
            for path in directory.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}\.(dpapi|partial)", path.name):
                    continue
                self._guard(path)
                if not path.is_file():
                    raise indisponivel()
                ref = (str(UUID(directory.name)), path.stem)
                if path.suffix == ".partial" or ref not in referencias:
                    yield path

    def pendente(self, referencias):
        """Consulta sem exclusão; incerteza no armazenamento mantém o aviso."""
        try:
            return next(self._orfaos(referencias), None) is not None
        except OSError, ValueError, NegocioError:
            return True

    def limpar(self, referencias):
        """Chamado somente sob o lock do repositório, após commit ou na recuperação."""
        try:
            # Nenhum rmtree: somente arquivos controlados, sem seguir links.
            for path in self._orfaos(referencias):
                path.unlink()
        except OSError, ValueError:
            raise indisponivel() from None
