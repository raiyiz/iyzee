import struct

import numpy as np
import pytest

from iyzee.scope import LeCroy
from iyzee.vicp import VICP_DATA_FLAG, VICP_EOI_FLAG, VICPTransport


class FragmentingFakeSocket:
    """Fake TCP socket returning data in small arbitrary chunks."""

    def __init__(self, data: bytes = b"", chunk_size: int = 3):
        self._buf = bytearray(data)
        self._chunk_size = chunk_size
        self.sent = bytearray()
        self.connected_to = None
        self.closed = False
        self.timeout = None

    def connect(self, address):
        self.connected_to = address

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True

    def recv(self, size: int) -> bytes:
        take = min(size, self._chunk_size, len(self._buf))
        chunk = bytes(self._buf[:take])
        del self._buf[:take]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)


def vicp_frame(flag: int, payload: bytes) -> bytes:
    return VICPTransport.encode_header(flag, len(payload)) + payload


def make_scope(fake: FragmentingFakeSocket) -> LeCroy:
    return LeCroy(transport=VICPTransport("scope", socket_factory=lambda *_: fake))


def test_vicp_header_round_trip():
    header = VICPTransport.encode_header(VICP_DATA_FLAG | VICP_EOI_FLAG, 1234)
    flags, version, sequence, length = VICPTransport.decode_header(header)

    assert flags == VICP_DATA_FLAG | VICP_EOI_FLAG
    assert version == 1
    assert sequence == 1
    assert length == 1234


def test_transport_reassembles_fragmented_frame():
    fake = FragmentingFakeSocket(vicp_frame(VICP_EOI_FLAG, b"hello"), chunk_size=2)
    transport = VICPTransport("scope", socket_factory=lambda *_: fake)
    transport.connect()

    frame = transport.receive_frame()

    assert frame.flags == VICP_EOI_FLAG
    assert frame.payload == b"hello"
    assert frame.eoi
    assert not frame.data
    assert frame.header_version == 1
    assert frame.sequence == 1
    assert fake.connected_to == ("scope", 1861)
    assert fake.timeout == 5.0


def test_transport_receive_message_stops_on_eoi_with_data_flag_set():
    response = vicp_frame(VICP_DATA_FLAG, b"hello ") + vicp_frame(
        VICP_DATA_FLAG | VICP_EOI_FLAG, b"world"
    )
    fake = FragmentingFakeSocket(response, chunk_size=2)
    transport = VICPTransport("scope", socket_factory=lambda *_: fake)
    transport.connect()

    flags, payload = transport.receive_message()

    assert flags == VICP_DATA_FLAG | VICP_EOI_FLAG
    assert payload == b"hello world"


def test_transport_send_uses_single_complete_write():
    fake = FragmentingFakeSocket()
    transport = VICPTransport("scope", socket_factory=lambda *_: fake)
    transport.connect()

    transport.send_ascii("C1:VDIV 1.0")

    flags, version, sequence, length = VICPTransport.decode_header(fake.sent[:8])
    assert flags == VICP_DATA_FLAG | VICP_EOI_FLAG
    assert version == 1
    assert sequence == 1
    assert length == len("C1:VDIV 1.0")
    assert fake.sent[8:] == b"C1:VDIV 1.0"


def test_transport_closes_socket_after_connect_failure():
    created = []

    def failing_socket(*_):
        class FailingSocket(FragmentingFakeSocket):
            def connect(self, address):
                raise OSError("offline")

        sock = FailingSocket()
        created.append(sock)
        return sock

    transport = VICPTransport("scope", socket_factory=failing_socket)
    with pytest.raises(OSError, match="offline"):
        transport.connect()
    assert not transport.connected
    assert created[0].closed


def test_transport_context_manager_closes_socket():
    fake = FragmentingFakeSocket()
    transport = VICPTransport("scope", socket_factory=lambda *_: fake)

    with transport:
        assert transport.connected
    assert not transport.connected
    assert fake.closed


def test_lecroy_context_manager_uses_transport_lifecycle():
    fake = FragmentingFakeSocket()
    scope = make_scope(fake)

    with scope:
        assert scope.connected
    assert not scope.connected
    assert fake.closed


def waveform_responses(data: bytes, *, final_data_frame: bool = False) -> bytes:
    preamble = b"x" * 27 + b"#9" + f"{len(data):09d}".encode("ascii")
    if final_data_frame:
        return preamble + vicp_frame(VICP_DATA_FLAG | VICP_EOI_FLAG, data)
    return preamble + vicp_frame(VICP_DATA_FLAG, data) + vicp_frame(VICP_EOI_FLAG, b"\n")


def test_get_data_bytes_reassembles_odd_length_waveform():
    fake = FragmentingFakeSocket(waveform_responses(bytes([0, 1, 255])), chunk_size=2)
    scope = make_scope(fake)
    scope.connect()

    result = scope.getDataBytes(channel="C1", block="DAT1")

    assert result == [(0,), (1,), (-1,)]


def test_get_data_words_accepts_final_data_eoi_frame():
    data = struct.pack("<2h", -123, 456)
    fake = FragmentingFakeSocket(waveform_responses(data, final_data_frame=True), chunk_size=2)
    scope = make_scope(fake)
    scope.connect()

    result = scope.getDataWords(channel="C1", block="DAT1")

    assert result == (-123, 456)


def test_get_data_words_reassembles_fragmented_waveform():
    data = struct.pack("<2h", -123, 456)
    fake = FragmentingFakeSocket(waveform_responses(data), chunk_size=2)
    scope = make_scope(fake)
    scope.connect()

    result = scope.getDataWords(channel="C1", block="DAT1")

    assert result == (-123, 456)


def test_get_data_words_rejects_malformed_waveform_header():
    fake = FragmentingFakeSocket(b"x" * 38, chunk_size=3)
    scope = make_scope(fake)
    scope.connect()

    with pytest.raises(RuntimeError, match="incorrectly returned waveform header"):
        scope.getDataWords(channel="C1", block="DAT1")


def test_get_data_words_rejects_short_waveform_data():
    data = struct.pack("<h", 123)
    preamble = b"x" * 27 + b"#9" + f"{len(data) + 2:09d}".encode("ascii")
    fake = FragmentingFakeSocket(
        preamble + vicp_frame(VICP_DATA_FLAG, data) + vicp_frame(VICP_EOI_FLAG, b"\n"),
        chunk_size=2,
    )
    scope = make_scope(fake)
    scope.connect()

    with pytest.raises(RuntimeError, match="expected 4 waveform bytes, got 2"):
        scope.getDataWords(channel="C1", block="DAT1")


def test_get_data_floats_applies_vertical_scaling_and_unit():
    data = struct.pack("<2h", 100, -50)
    responses = (
        waveform_responses(data)
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 0.25"\n')
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 2.0"\n')
        + vicp_frame(VICP_EOI_FLAG, b'Unit Name = V"\n')
    )
    fake = FragmentingFakeSocket(responses, chunk_size=2)
    scope = make_scope(fake)
    scope.connect()

    unit, values = scope.getDataFloats(channel="C1", block="DAT1")

    assert unit == "V"
    np.testing.assert_allclose(values, np.array([199.75, -100.25]))


def test_identify_reads_scope_id():
    fake = FragmentingFakeSocket(
        vicp_frame(VICP_EOI_FLAG, b"LeCroy,WaveSurfer,452,1\n"), chunk_size=2
    )
    scope = make_scope(fake)
    scope.connect()

    assert scope.identify() == "LeCroy,WaveSurfer,452,1\n"
    assert fake.sent[8:] == b"*IDN?"


def test_acquire_waveform_builds_physical_time_axis():
    data = struct.pack("<2h", 10, 20)
    responses = (
        waveform_responses(data)
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 0.0"\n')
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 2.0"\n')
        + vicp_frame(VICP_EOI_FLAG, b'Unit Name = V"\n')
        + vicp_frame(VICP_EOI_FLAG, b'Unit Name = s"\n')
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 0.25"\n')
        + vicp_frame(VICP_EOI_FLAG, b'VALUE: 0.001"\n')
    )
    fake = FragmentingFakeSocket(responses, chunk_size=2)
    scope = make_scope(fake)
    scope.connect()

    waveform = scope.acquire_waveform("C1")

    assert waveform.channel == "C1"
    assert waveform.y_unit == "V"
    assert waveform.x_unit == "s"
    np.testing.assert_allclose(waveform.y, [20.0, 40.0])
    np.testing.assert_allclose(waveform.x, [0.25, 0.251])
