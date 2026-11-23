"""Auxiliar exclusivo de testes nativos; não é provedor de mensagens."""

import msvcrt
import os
import struct
import subprocess
import sys
import time


def main():
    mode = sys.argv[1]
    if mode == "tree_child":
        depth = int(sys.argv[2])
        if depth:
            subprocess.Popen(
                [sys._base_executable, "-B", __file__, "tree_child", str(depth - 1)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        time.sleep(120)
        raise SystemExit

    from app.runtime.control_protocol import HEADER, Kind, read_frame, write_frame

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    kind, init = read_frame(sys.stdin.buffer)
    assert kind == Kind.INIT
    execution = init[:16]
    kind, lifetime = read_frame(sys.stdin.buffer)
    assert kind == Kind.LIFETIME and lifetime[:16] == execution

    def emit(kind, suffix=b""):
        write_frame(sys.stdout.buffer, kind, execution + suffix)

    if mode == "no_hello":
        time.sleep(120)
    elif mode == "arbitrary":
        sys.stdout.buffer.write(b"not a control frame\n")
        sys.stdout.buffer.flush()
        time.sleep(120)
    elif mode == "oversized":
        sys.stdout.buffer.write(HEADER.pack(b"ED5P", 1, Kind.HELLO, 513))
        sys.stdout.buffer.flush()
        time.sleep(120)
    elif mode == "version":
        sys.stdout.buffer.write(HEADER.pack(b"ED5P", 2, Kind.HELLO, 20))
        sys.stdout.buffer.flush()
        time.sleep(120)
    elif mode == "truncated":
        sys.stdout.buffer.write(b"ED5")
        sys.stdout.buffer.flush()
        sys.stdout.close()
        raise SystemExit
    elif mode == "out_of_order":
        emit(Kind.READY, struct.pack("<H", 12345))
        time.sleep(120)
    elif mode == "wrong_identity":
        write_frame(sys.stdout.buffer, Kind.HELLO, bytes(16) + struct.pack("<I", os.getpid()))
        time.sleep(120)
    elif mode in {"outside_job", "zero_pid"}:
        pid = int(sys.argv[2]) if mode == "outside_job" else 0
        emit(Kind.HELLO, struct.pack("<I", pid))
        time.sleep(120)
    else:
        emit(Kind.HELLO, struct.pack("<I", os.getpid()))
        if mode == "duplicate":
            emit(Kind.HELLO, struct.pack("<I", os.getpid()))
            time.sleep(120)
        emit(Kind.PREPARING)
        if mode == "no_ready":
            time.sleep(120)
        if mode == "arguments":
            assert sys.argv[2] == 'espaços "aspas" final\\'
        if mode in {"tree", "ignore_stop"}:
            subprocess.Popen(
                [sys._base_executable, "-B", __file__, "tree_child", "1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        emit(Kind.READY, struct.pack("<H", 12345))
        if mode == "ignore_stop":
            time.sleep(120)
        if mode == "stderr":
            sys.stderr.write("diagnostico de teste\n" * 10000)
            sys.stderr.flush()
        kind, payload = read_frame(sys.stdin.buffer)
        if kind == Kind.QUIESCE:
            assert payload == execution
            kind, payload = read_frame(sys.stdin.buffer)
        assert kind == Kind.STOP and payload == execution
        emit(Kind.STOPPED)


if __name__ == "__main__":
    main()
