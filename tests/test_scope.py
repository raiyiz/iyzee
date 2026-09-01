import socket
import struct

import pytest

from iyzee.scope import LeCroy


class FragmentingFakeSocket:
    """A fake socket whose recv() returns data in small, arbitrary chunks.

    Used to prove that reads assemble a fixed-length message correctly even
    when the underlying transport delivers it split across multiple TCP
    segments, which a single recv() call is not guaranteed to avoid.
    """

    def __init__(self, data: bytes, chunk_size: int = 3):
        self._buf = data
        self._chunk_size = chunk_size

    def recv(self, n: int) -> bytes:
        take = min(n, self._chunk_size, len(self._buf))
        chunk, self._buf = self._buf[:take], self._buf[take:]
        return chunk


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
    header = struct.pack("B3BI", 0x80, 1, 0, 0, socket.htonl(42))
    scope.s = FragmentingFakeSocket(header, chunk_size=2)

    flag, length = scope._LeCroy__getHeader()

    assert flag == 0x80
    assert length == 42
