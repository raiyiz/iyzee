import socket
import struct

import numpy as np
import pytest

from iyzee.scope import (
    Channel,
    Coupling,
    LeCroy,
    LeCroyTimeoutError,
    MathChannel,
    TriggerCoupling,
    TriggerMode,
    TriggerSlope,
)


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


# -- timeouts: connect(), _recv_exact(), send() must not block forever --------------------


class _TimingOutSocket:
    """A fake socket that always times out, reporting a fixed ``gettimeout()``
    the way a real socket would once ``settimeout()`` has been applied."""

    def __init__(self, timeout: float = 3.0):
        self._timeout = timeout

    def gettimeout(self) -> float:
        return self._timeout

    def recv(self, n: int) -> bytes:
        raise TimeoutError("timed out")

    def send(self, data: bytes) -> int:
        raise TimeoutError("timed out")


class _PartialThenTimeoutSocket:
    """Delivers ``data`` two bytes at a time, then times out on every read
    after that — for checking a timeout mid-transfer reports real progress,
    not just "it failed"."""

    def __init__(self, data: bytes, timeout: float = 1.5):
        self._buf = data
        self._timeout = timeout

    def gettimeout(self) -> float:
        return self._timeout

    def recv(self, n: int) -> bytes:
        if not self._buf:
            raise TimeoutError("timed out")
        chunk, self._buf = self._buf[:2], self._buf[2:]
        return chunk


def test_recv_exact_raises_lecroy_timeout_not_a_bare_timeout():
    sock = _TimingOutSocket(timeout=3.0)

    with pytest.raises(LeCroyTimeoutError, match=r"3\.0s \(0/10 bytes received\)"):
        LeCroy._recv_exact(sock, 10)


def test_recv_exact_reports_how_far_it_got_before_timing_out():
    sock = _PartialThenTimeoutSocket(b"abcd", timeout=1.5)

    with pytest.raises(LeCroyTimeoutError, match=r"1\.5s \(4/10 bytes received\)"):
        LeCroy._recv_exact(sock, 10)


def test_send_raises_lecroy_timeout_when_the_header_write_stalls():
    scope = LeCroy()
    scope.s = _TimingOutSocket(timeout=2.0)

    with pytest.raises(LeCroyTimeoutError, match=r"writing header after 2\.0s"):
        scope.send("C1:VDIV 1.0")


class _ConnectTimeoutSocket:
    """Simulates a TCP handshake that never completes (e.g. the scope is
    off, or a firewall is silently dropping the connection)."""

    def __init__(self, *args, **kwargs):
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        raise TimeoutError("timed out")

    def close(self):
        self.closed = True


def test_connect_raises_lecroy_timeout_instead_of_hanging(monkeypatch):
    fake_socket = _ConnectTimeoutSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    scope = LeCroy()
    with pytest.raises(LeCroyTimeoutError, match=rf"within {LeCroy.MAX_TCP_CONNECT}s"):
        scope.connect("10.0.0.1")

    # The handshake was bounded by MAX_TCP_CONNECT specifically...
    assert fake_socket.timeout == LeCroy.MAX_TCP_CONNECT
    # ...and the half-open socket wasn't leaked.
    assert fake_socket.closed
    assert scope.CONNECTED is False


class _ConnectableSocket:
    """A fake socket that connects successfully, recording every
    ``settimeout()`` call so the test can check both timeouts get applied."""

    def __init__(self, *args, **kwargs):
        self.timeouts: list[float] = []
        self.connected_to = None

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected_to = address


def test_connect_bounds_the_handshake_then_the_ongoing_socket_timeout(monkeypatch):
    fake_socket = _ConnectableSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    scope = LeCroy()
    scope.connect("10.0.0.1")

    assert scope.CONNECTED is True
    assert fake_socket.connected_to == ("10.0.0.1", LeCroy.LECROY_SERVER_PORT)
    # settimeout() is called twice: once (MAX_TCP_CONNECT) before connect()
    # so the handshake itself can't hang, and again (MAC_TCP_READ) once
    # connected, so every later send()/recv() inherits a bound too.
    assert fake_socket.timeouts == [LeCroy.MAX_TCP_CONNECT, LeCroy.MAC_TCP_READ]


def test_connect_accepts_explicit_timeouts(monkeypatch):
    fake_socket = _ConnectableSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    scope = LeCroy()
    scope.connect("10.0.0.1", delayval=7.0, connect_timeout=1.0)

    assert fake_socket.timeouts == [1.0, 7.0]
    assert scope.SOCK_TIMEOUT == 7.0


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
    responses = (
        preamble
        + vicp_frame(0x80, data)
        + vicp_frame(0x01, b"\n")
        + vicp_frame(0x01, b'VALUE: 0.25"\n')
        + vicp_frame(0x01, b'VALUE: 2.0"\n')
        + vicp_frame(0x01, b'Unit Name = V"\n')
    )
    scope.s = FragmentingFakeSocket(responses, chunk_size=2)

    unit, values = scope.getDataFloats(channel="C1", block="DAT1")

    assert unit == "V"
    np.testing.assert_allclose(values, np.array([199.75, -100.25]))


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


# -- channel / trigger / math control --------------------------------------------------------


def sent_message(sock: FragmentingFakeSocket) -> str:
    """Decode the ascii payload of the (single) VICP frame `sock` received."""
    _flag, _r1, _r2, _r3, length = struct.unpack("B3BI", bytes(sock.sent[:8]))
    return bytes(sock.sent[8 : 8 + socket.ntohl(length)]).decode("ascii")


@pytest.mark.parametrize(
    "call,expected",
    [
        (lambda s: s.set_volts_per_div(Channel.C1, 0.5), "C1:VOLT_DIV 0.5"),
        (lambda s: s.set_offset(Channel.C2, -0.3), "C2:OFFSET -0.3"),
        (lambda s: s.set_coupling(Channel.C1, Coupling.DC_50), "C1:COUPLING D50"),
        (lambda s: s.set_coupling(Channel.C1, Coupling.AC_1M), "C1:COUPLING A1M"),
        (lambda s: s.set_attenuation(Channel.C3, 10), "C3:ATTENUATION 10"),
        (lambda s: s.set_bandwidth_limit(Channel.C1, "20MHZ"), "C1:BANDWIDTH_LIMIT 20MHZ"),
        (lambda s: s.set_trace_display(Channel.C1, True), "C1:TRACE ON"),
        (lambda s: s.set_trace_display(Channel.C1, False), "C1:TRACE OFF"),
        (lambda s: s.set_trace_display(MathChannel.F1, True), "F1:TRACE ON"),
        (lambda s: s.set_invert(Channel.C2, True), "C2:INVERT_SET ON"),
        (lambda s: s.set_trigger_mode(TriggerMode.SINGLE), "TRIG_MODE SINGLE"),
        (lambda s: s.set_trigger_source(Channel.C1), "TRIG_SELECT EDGE,SR,C1"),
        (lambda s: s.set_trigger_level(Channel.C1, 1.5), "C1:TRIG_LEVEL 1.5"),
        (lambda s: s.set_trigger_slope(Channel.C1, TriggerSlope.NEGATIVE), "C1:TRIG_SLOPE NEG"),
        (
            lambda s: s.set_trigger_coupling(Channel.C1, TriggerCoupling.HF_REJECT),
            "C1:TRIG_COUPLING HFREJ",
        ),
        (lambda s: s.set_trigger_delay(-1e-6), "TRIG_DELAY -1e-06"),
        (lambda s: s.set_math_equation(MathChannel.F1, "C1-C2"), "F1:DEFINE EQN,'C1-C2'"),
        (
            lambda s: s.set_math_difference(MathChannel.F1, Channel.C1, Channel.C2),
            "F1:DEFINE EQN,'C1-C2'",
        ),
        (lambda s: s.set_math_average(MathChannel.F2, Channel.C3), "F2:DEFINE EQN,'AVG(C3)'"),
        (lambda s: s.set_math_fft(MathChannel.F1, Channel.C1), "F1:DEFINE EQN,'FFT(C1)'"),
    ],
)
def test_setters_send_the_documented_command(call, expected):
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(b"")

    call(scope)

    assert sent_message(scope.s) == expected


def test_query_sends_the_command_and_returns_the_trimmed_response():
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(vicp_frame(0x01, b"C1:COUPLING D50 \n"))

    response = scope.query("C1:COUPLING?")

    assert sent_message(scope.s) == "C1:COUPLING?"
    assert response == "C1:COUPLING D50"  # trailing whitespace/newline stripped


@pytest.mark.parametrize(
    "call,command,response,expected",
    [
        (
            lambda s: s.get_coupling(Channel.C1),
            "C1:COUPLING?",
            b"C1:COUPLING A1M\n",
            "C1:COUPLING A1M",
        ),
        (
            lambda s: s.get_trace_display(Channel.C2),
            "C2:TRACE?",
            b"C2:TRACE ON\n",
            "C2:TRACE ON",
        ),
        (
            lambda s: s.get_trigger_mode(),
            "TRIG_MODE?",
            b"TRIG_MODE AUTO\n",
            "TRIG_MODE AUTO",
        ),
        (
            lambda s: s.get_trigger_slope(Channel.C1),
            "C1:TRIG_SLOPE?",
            b"C1:TRIG_SLOPE NEG\n",
            "C1:TRIG_SLOPE NEG",
        ),
        (
            lambda s: s.get_trigger_coupling(Channel.C1),
            "C1:TRIG_COUPLING?",
            b"C1:TRIG_COUPLING DC\n",
            "C1:TRIG_COUPLING DC",
        ),
        (
            lambda s: s.get_math_equation(MathChannel.F1),
            "F1:DEFINE?",
            b"F1:DEFINE EQN,'C1-C2'\n",
            "F1:DEFINE EQN,'C1-C2'",
        ),
    ],
)
def test_getters_query_and_return_the_response(call, command, response, expected):
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(vicp_frame(0x01, response))

    result = call(scope)

    assert sent_message(scope.s) == command
    assert result == expected
