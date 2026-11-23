"""Armazena somente ciphertext DPAPI da sessao Baileys fora do repositorio."""

import base64
import json
import os
import stat
import sys
from pathlib import Path
from uuid import UUID

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend" / "src"))

from app.contas_envio.segredos import dpapi

CHUNK = 59_000
MAX_SESSION = 16 * 1024 * 1024
ACCOUNT = os.environ.get("EDISK_WHATSAPP_ACCOUNT_ID", "")
if ACCOUNT:
    parsed_account = UUID(ACCOUNT)
    if str(parsed_account) != ACCOUNT or parsed_account.version != 4:
        raise RuntimeError
ROOT_NAME = "whatsapp-baileys" if ACCOUNT else "poc-whatsapp-baileys"
ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "eDisk" / ROOT_NAME
if ACCOUNT:
    ROOT /= ACCOUNT
PURPOSE = f"whatsapp-baileys-session:{ACCOUNT}" if ACCOUNT else "whatsapp-baileys-poc-session"
CHUNK_HEADER = b"edisk-whatsapp-baileys-v1\0"
if ACCOUNT:
    CHUNK_HEADER += ACCOUNT.encode("ascii") + b"\0"
SESSION = ROOT / "session.dpapi"
PARTIAL = ROOT / "session.partial"


def _guard(path: Path):
    if not os.environ.get("LOCALAPPDATA") or (path != ROOT and ROOT not in path.parents):
        raise RuntimeError
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise RuntimeError


def _write():
    raw = sys.stdin.buffer.read(MAX_SESSION + 1)
    if not raw or len(raw) > MAX_SESSION:
        raise RuntimeError
    _guard(ROOT)
    ROOT.mkdir(parents=True, exist_ok=True)
    _guard(ROOT)
    parts = [raw[i : i + CHUNK] for i in range(0, len(raw), CHUNK)]
    encrypted = [
        base64.b64encode(
            dpapi(CHUNK_HEADER + index.to_bytes(4, "big") + len(parts).to_bytes(4, "big") + part)
        ).decode("ascii")
        for index, part in enumerate(parts)
    ]
    envelope = json.dumps(
        {"format": 1, "purpose": PURPOSE, "chunks": encrypted},
        separators=(",", ":"),
    ).encode("ascii")
    _guard(PARTIAL)
    PARTIAL.unlink(missing_ok=True)
    with PARTIAL.open("xb") as stream:
        stream.write(envelope)
        stream.flush()
        os.fsync(stream.fileno())
    _guard(SESSION)
    os.replace(PARTIAL, SESSION)


def _read():
    _guard(SESSION)
    if not SESSION.exists():
        return
    with SESSION.open("rb") as stream:
        envelope = json.loads(stream.read(MAX_SESSION * 2))
    if (
        envelope.get("format") != 1
        or envelope.get("purpose") != PURPOSE
        or not isinstance(envelope.get("chunks"), list)
        or not envelope["chunks"]
    ):
        raise RuntimeError
    parts = []
    total = len(envelope["chunks"])
    for index, item in enumerate(envelope["chunks"]):
        part = dpapi(base64.b64decode(item, validate=True), decrypt=True)
        header_size = len(CHUNK_HEADER) + 8
        if (
            len(part) <= header_size
            or part[: len(CHUNK_HEADER)] != CHUNK_HEADER
            or int.from_bytes(part[len(CHUNK_HEADER) : len(CHUNK_HEADER) + 4], "big") != index
            or int.from_bytes(part[len(CHUNK_HEADER) + 4 : header_size], "big") != total
        ):
            raise RuntimeError
        parts.append(part[header_size:])
    raw = b"".join(parts)
    if not raw or len(raw) > MAX_SESSION:
        raise RuntimeError
    sys.stdout.buffer.write(raw)


def _clear():
    for path in (PARTIAL, SESSION):
        _guard(path)
        path.unlink(missing_ok=True)


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    if command == "read":
        _read()
    elif command == "write":
        _write()
    elif command == "clear":
        _clear()
    else:
        raise RuntimeError


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - fronteira do processo não expõe detalhes sensíveis
        print("Armazenamento DPAPI indisponivel.", file=sys.stderr)
        raise SystemExit(1) from None

