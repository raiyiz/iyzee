import socket
import struct

import numpy as np
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


def test_send_serializes_vicp_header_and_message():
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(b"")

    scope.send("C1:VDIV 1.0")

    flag, reserved_1, reserved_2, reserved_3, length = struct.unpack("B3BI", scope.s.sent[:8])
    assert flag == LeCroy.LECROY_DATA_FLAG | LeCroy.LECROY_EOI_FLAG
    assert (reserved_1, reserved_2, reserved_3) == (1, 0, 0)
    assert socket.ntohl(length) == len("C1:VDIV 1.0")
    assert scope.s.sent[8:] == b"C1:VDIV 1.0"


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


def test_get_data_floats_applies_vertical_scaling_and_unit():
    scope = LeCroy()
    data = struct.pack("<2h", 100, -50)
    preamble = b"x" * 27 + b"#9" + f"{len(data):09d}".encode("ascii")
    inspect_responses = b"".join(
        [
            vicp_frame(0x01, b'VALUE: 2.0"\n'),
            vicp_frame(0x01, b'Unit Name = V"\n'),
        ]
    )
    # getDataFloats calls getDataWords first, then three inspect queries.
    responses = (
        preamble
        + vicp_frame(0x80, data)
        + vicp_frame(0x01, b"\n")
        + vicp_frame(0x01, b'VALUE: 0.25"\n')
        + inspect_responses
    )
    scope.s = FragmentingFakeSocket(responses, chunk_size=2)

    unit, values = scope.getDataFloats(channel="C1", block="DAT1")

    assert unit == "V"
    np.testing.assert_allclose(values, np.array([197.75, -100.25]))


def test_get_horizontal_properties_reads_unit_offset_and_interval():
    scope = LeCroy()
    responses = b"".join(
        [
            vicp_frame(0x01, b'Unit Name = s"\n'),
            vicp_frame(0x01, b'VALUE: 0.25"\n'),
            vicp_frame(0x01, b'VALUE: 0.001"\n'),
        ]
    )
    scope.s = FragmentingFakeSocket(responses, chunk_size=2)

    unit, offset, interval = scope.getHorProperties(channel="C1")

    assert unit == "s"
    assert offset == pytest.approx(0.25)
    assert interval == pytest.approx(0.001)
