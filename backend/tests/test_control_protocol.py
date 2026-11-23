import io

import pytest

from app.runtime.control_protocol import HEADER, Kind, ProtocolError, read_frame, write_frame


@pytest.mark.parametrize(
    "kind,size",
    [
        (Kind.INIT, 48),
        (Kind.HELLO, 20),
        (Kind.PREPARING, 16),
        (Kind.READY, 18),
        (Kind.ERROR, 18),
        (Kind.STOP, 16),
        (Kind.STOPPED, 16),
        (Kind.LIFETIME, 32),
        (Kind.QUIESCE, 16),
    ],
)
def test_roundtrip(kind, size):
    stream = io.BytesIO()
    write_frame(stream, kind, bytes(size))
    stream.seek(0)
    assert read_frame(stream) == (kind, bytes(size))


@pytest.mark.parametrize(
    "frame",
    [
        b"",
        b"ED5",
        b"arbitrary stdout",
        HEADER.pack(b"ED5P", 2, 1, 48),
        HEADER.pack(b"ED5P", 1, 99, 16),
        HEADER.pack(b"ED5P", 1, 1, 513),
        HEADER.pack(b"ED5P", 1, 1, 47),
        HEADER.pack(b"ED5P", 1, 1, 48) + b"x",
    ],
)
def test_rejects_invalid_frames(frame):
    with pytest.raises(ProtocolError):
        read_frame(io.BytesIO(frame))


def test_rejects_invalid_output_size():
    with pytest.raises(ProtocolError):
        write_frame(io.BytesIO(), Kind.READY, b"")


def test_eof_after_header_is_always_truncation():
    from app.runtime.control_protocol import EndOfPipe

    with pytest.raises(EndOfPipe) as error:
        read_frame(io.BytesIO(HEADER.pack(b"ED5P", 1, Kind.STOP, 16)))
    assert error.value.partial


def test_fragmented_read_and_partial_write():
    class Fragmented(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 1))

        def write(self, data):
            return super().write(data[:1])

    stream = Fragmented()
    payload = bytes(range(48))
    write_frame(stream, Kind.INIT, payload)
    stream.seek(0)
    assert read_frame(stream) == (Kind.INIT, payload)
