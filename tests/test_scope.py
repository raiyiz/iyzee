import socket
import struct
import threading

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
    VICPFrame,
    VICPProtocolError,
    VICPTimeoutError,
    VICPTransport,
)
from iyzee.vicp import VICP_DATA_FLAG, VICP_EOI_FLAG, recv_exact


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

    result = recv_exact(sock, len(payload))

    assert result == payload


def test_recv_exact_raises_on_closed_connection():
    sock = FragmentingFakeSocket(b"short", chunk_size=3)

    with pytest.raises(ConnectionError):
        recv_exact(sock, 100)

# -- timeouts: connect(), recv_exact(), send() must not block forever --------------------


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
        recv_exact(sock, 10, timeout_error=LeCroyTimeoutError)


def test_recv_exact_reports_how_far_it_got_before_timing_out():
    sock = _PartialThenTimeoutSocket(b"abcd", timeout=1.5)

    with pytest.raises(LeCroyTimeoutError, match=r"1\.5s \(4/10 bytes received\)"):
        recv_exact(sock, 10, timeout_error=LeCroyTimeoutError)


def test_vicp_transport_transaction_is_reentrant():
    transport = VICPTransport()

    with transport.transaction():
        with transport.transaction():
            assert transport.connected is False


def test_vicp_transport_transactions_are_serialized():
    transport = VICPTransport()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with transport.transaction():
            first_entered.set()
            assert release_first.wait(2.0)

    def second() -> None:
        assert first_entered.wait(2.0)
        with transport.transaction():
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    assert first_entered.wait(2.0)
    second_thread.start()

    assert not second_entered.wait(0.05)
    release_first.set()

    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()


def test_send_raises_lecroy_timeout_when_the_header_write_stalls():
    scope = LeCroy()
    scope.s = _TimingOutSocket(timeout=2.0)

    with pytest.raises(
        LeCroyTimeoutError,
        match=r"no response after 2\.0s \(0/\d+ bytes sent\)",
    ):
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


def test_vicp_transport_failed_connect_leaves_state_unpublished(monkeypatch):
    fake_socket = _ConnectTimeoutSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    transport = VICPTransport()

    with pytest.raises(VICPTimeoutError):
        transport.connect("10.0.0.1")

    assert not transport.connected
    assert transport.address is None


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
    assert scope.connected is False


class _ConnectableSocket:
    """A fake socket that connects successfully, recording every
    ``settimeout()`` call so the test can check both timeouts get applied."""

    def setsockopt(self, *args):
        pass

    def __init__(self, *args, **kwargs):
        self.timeouts: list[float] = []
        self.connected_to = None
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected_to = address

    def close(self) -> None:
        self.closed = True


def test_connect_bounds_the_handshake_then_the_ongoing_socket_timeout(monkeypatch):
    fake_socket = _ConnectableSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    scope = LeCroy()
    scope.connect("10.0.0.1")

    assert scope.connected is True
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


def test_disconnect_clears_connection_state(monkeypatch):
    fake_socket = _ConnectableSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_socket)

    scope = LeCroy()
    scope.connect("10.0.0.1")
    assert scope.address == "10.0.0.1"

    scope.disconnect()

    assert fake_socket.closed
    assert scope.connected is False
    assert scope.connected is False
    assert scope.address is None
    assert scope.s is None


def test_vicp_transport_reads_fragmented_frame():
    transport = VICPTransport()
    transport.attach_socket(FragmentingFakeSocket(vicp_frame(0x81, b"hello"), chunk_size=2))

    frame = transport.read_frame()

    assert isinstance(frame, VICPFrame)
    assert frame.flags == 0x81
    assert frame.is_data
    assert frame.is_eoi
    assert frame.payload == b"hello"


def test_vicp_transport_rejects_unknown_header_version():
    transport = VICPTransport()
    transport.attach_socket(
        FragmentingFakeSocket(struct.pack("B3BI", 0x01, 2, 0, 0, 0), chunk_size=2)
    )

    with pytest.raises(VICPProtocolError, match="unsupported VICP header version 2"):
        transport.read_frame()


def test_read_all_reassembles_fragmented_vicp_frames():
    scope = LeCroy()
    response = vicp_frame(0x80, b"hello ") + vicp_frame(0x01, b"world")
    scope.s = FragmentingFakeSocket(response, chunk_size=2)

    flag, text = scope.readAll()

    assert flag == 0x01
    assert text == "hello world"


def test_vicp_transport_handles_partial_sends():
    class PartialSendSocket(FragmentingFakeSocket):
        def send(self, data: bytes) -> int:
            count = min(2, len(data))
            self.sent.extend(data[:count])
            return count

    sock = PartialSendSocket(b"")
    transport = VICPTransport()
    transport.attach_socket(sock)

    transport.send_command("C1:TEST")

    assert b"C1:TEST" in bytes(sock.sent)


def test_vicp_transport_rejects_zero_progress_send():
    class StalledSocket(FragmentingFakeSocket):
        def send(self, data: bytes) -> int:
            return 0

    transport = VICPTransport()
    transport.attach_socket(StalledSocket(b""))

    with pytest.raises(ConnectionError, match="no progress"):
        transport.send_command("C1:TEST")


def test_send_serializes_vicp_header_and_message():
    scope = LeCroy()
    scope.s = FragmentingFakeSocket(b"")

    scope.send("C1:VDIV 1.0")

    sock = scope.s
    assert isinstance(sock, FragmentingFakeSocket)
    flag, reserved_1, reserved_2, reserved_3, length = struct.unpack("B3BI", sock.sent[:8])
    assert flag == VICP_DATA_FLAG | VICP_EOI_FLAG
    assert (reserved_1, reserved_2, reserved_3) == (1, 0, 0)
    assert socket.ntohl(length) == len("C1:VDIV 1.0")
    assert sock.sent[8:] == b"C1:VDIV 1.0"


def test_get_data_bytes_reads_a_variable_length_definite_block():
    scope = LeCroy()
    response = (
        vicp_frame(0x80, b"C1:WF DAT1,#9")
        + vicp_frame(0x80, b"000000003")
        + vicp_frame(0x80, bytes([0, 1, 255]))
        + vicp_frame(0x81, b"\n")
    )
    scope.s = FragmentingFakeSocket(response, chunk_size=2)

    result = scope.getDataBytes(channel="C1", block="DAT1")

    assert result == [(0,), (1,), (-1,)]


def test_get_data_words_reads_definite_block_data_and_validates_word_size():
    scope = LeCroy()
    data = struct.pack("<2h", -123, 456)
    response = (
        vicp_frame(0x80, b"C1:WF DAT1,#9000000004")
        + vicp_frame(0x80, data[:1])
        + vicp_frame(0x80, data[1:])
        + vicp_frame(0x81, b"\n")
    )
    scope.s = FragmentingFakeSocket(response, chunk_size=2)

    result = scope.getDataWords(channel="C1", block="DAT1")

    assert result == (-123, 456)


def test_get_data_floats_applies_vertical_scaling_and_unit():
    scope = LeCroy()
    data = struct.pack("<2h", 100, -50)
    responses = (
        vicp_frame(0x80, b"C1:WF DAT1,#9000000004")
        + vicp_frame(0x80, data)
        + vicp_frame(0x81, b"\n")
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


def test_get_data_floats_detailed_retains_raw_codes_and_calibration():
    scope = LeCroy()
    data = struct.pack("<2h", 100, -50)
    responses = (
        vicp_frame(0x80, b"C1:WF DAT1,#9000000004")
        + vicp_frame(0x80, data)
        + vicp_frame(0x81, b"\n")
        + vicp_frame(0x01, b'VALUE: 0.25"\n')
        + vicp_frame(0x01, b'VALUE: 2.0"\n')
        + vicp_frame(0x01, b'Unit Name = V"\n')
    )
    scope.s = FragmentingFakeSocket(responses, chunk_size=2)

    detailed = scope.getDataFloatsDetailed(channel="C1", block="DAT1")

    assert detailed["unit"] == "V"
    np.testing.assert_array_equal(detailed["raw_codes"], [100, -50])
    np.testing.assert_allclose(detailed["values"], [199.75, -100.25])
    assert detailed["vertical_gain"] == 2.0
    assert detailed["vertical_offset"] == 0.25

# -- channel / trigger / math control --------------------------------------------------------


def sent_message(sock: object) -> str:
    """Decode the ASCII payload of the single VICP frame received."""
    sent = getattr(sock, "sent")
    _flag, _r1, _r2, _r3, length = struct.unpack("B3BI", bytes(sent[:8]))
    return bytes(sent[8 : 8 + socket.ntohl(length)]).decode("ascii")


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
            lambda s: s.get_volts_per_div(Channel.C1),
            "C1:VOLT_DIV?",
            b"C1:VOLT_DIV 5.00E-01V\n",
            "C1:VOLT_DIV 5.00E-01V",
        ),
        (
            lambda s: s.get_offset(Channel.C2),
            "C2:OFFSET?",
            b"C2:OFFSET 0.00E+00V\n",
            "C2:OFFSET 0.00E+00V",
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
            lambda s: s.get_trigger_source(),
            "TRIG_SELECT?",
            b"TRIG_SELECT EDGE,SR,C1,HT,OFF\n",
            "TRIG_SELECT EDGE,SR,C1,HT,OFF",
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
            lambda s: s.get_trigger_level(Channel.C1),
            "C1:TRIG_LEVEL?",
            b"C1:TRIG_LEVEL 0.00E+00V\n",
            "C1:TRIG_LEVEL 0.00E+00V",
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
