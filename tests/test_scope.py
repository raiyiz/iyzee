import socket
import struct

import pytest

from iyzee.scope import LeCroy


class FragmentingFakeSocket:
    """A fake socket whose recv() returns data in small, arbitrary chunks."""

    def __init__(self, data: bytes, chunk_size: int = 3):
        self._buf = data
        self._chunk_size = chunk_size
        self.sent = bytearray()

    def recv(self, n: int) -> bytes:
        take = min(n, self._chunk_size, len(self._buf))
        chunk, self._buf = self._buf[:take], self._buf[take:]
        return chunk

    def send(self, data: bytes) -> int:
        self.sent.extend(data)
        return len(data)


def vicp_frame(flag: int, payload: bytes) -> bytes:
    header = struct.pack("B3BI", flag, 1, 0, 0, socket.htonl(len(payload)))
    return header + payload


def test_recv_exact_reassembles_fragmented_reads():
    payload = b"0123456789ABCDEF"
    sock = FragmentingFakeSocket(payload, chunk_size=3)

    result = LeCroy._recv_exact(sock, len(payload))

    assert result == payload


def test_recv_exact_raises_on_closed_connection():
    sock = FragmentingFakeSocket(b"short", chunk_size=3)

    with pytest.raises(ConnectionError):
        LeCroy._recv_exact(sock, 100)


def test_get_header_assembles_fragmented_header():
    scope = LeCroy()
    header = vicp_frame(0x80, b"")[:8]
    scope.s = FragmentingFakeSocket(header, chunk_size=2)

    flag, length = scope._LeCroy__getHeader()

    assert flag == 0x80
    assert length == 0


def test_read_all_reassembles_fragmented_vicp_frames():
    scope = LeCroy()
    response = vicp_frame(0x80, b"hello ") + vicp_frame(0x01, b"world")
    scope.s = FragmentingFakeSocket(response, chunk_size=2)

    flag, text = scope.readAll()

    assert flag == 0x01
    assert text == "hello world"


def test_get_data_bytes_reassembles_fragmented_waveform():
    scope = LeCroy()
    preamble = b"x" * 38
    waveform = vicp_frame(0x80, bytes([0, 1, 255])) + vicp_frame(0x01, b"\n")
    scope.s = FragmentingFakeSocket(preamble + waveform, chunk_size=2)

    result = scope.getDataBytes(channel="C1", block="DAT1")

    assert result == [(0,), (1,), (-1,)]


def test_get_data_words_reassembles_fragmented_waveform():
    scope = LeCroy()
    data = struct.pack("<2h", -123, 456)
    preamble = b"x" * 27 + b"#9" + f"{len(data):09d}".encode("ascii")
    waveform = vicp_frame(0x80, data) + vicp_frame(0x01, b"\n")
    scope.s = FragmentingFakeSocket(preamble + waveform, chunk_size=2)

    result = scope.getDataWords(channel="C1", block="DAT1")

    assert result == (-123, 456)


def test_get_data_words_rejects_malformed_waveform_header():
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(b"x" * 38, chunk_size=3)

    with pytest.raises(RuntimeError, match="incorrectly returned header"):
        scope.getDataWords(channel="C1", block="DAT1")


def test_get_data_words_rejects_short_waveform_data():
    scope = LeCroy()
    data = struct.pack("<h", 123)
    preamble = b"x" * 27 + b"#9" + f"{len(data) + 2:09d}".encode("ascii")
    waveform = vicp_frame(0x80, data) + vicp_frame(0x01, b"\n")
    scope.s = FragmentingFakeSocket(preamble + waveform, chunk_size=2)

    with pytest.raises(AssertionError, match="Expected 4 bytes, got 2"):
        scope.getDataWords(channel="C1", block="DAT1")
