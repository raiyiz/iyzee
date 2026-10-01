"""Driver-level tests for :class:`~iyzee.scope.LeCroy`: commands sent, replies parsed.

Framing and the failure contract of the byte stream are in ``test_vicp.py``.
"""

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

    def __init__(self, data: bytes = b"", chunk_size: int = 3):
        self._buf = data
        self._chunk_size = chunk_size
        self.sent = bytearray()
        self.timeouts: list[float] = []

    def recv(self, n: int) -> bytes:
        take = min(n, self._chunk_size, len(self._buf))
        chunk, self._buf = self._buf[:take], self._buf[take:]
        return chunk

    def send(self, data: bytes) -> int:
        self.sent.extend(data)
        return len(data)

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def gettimeout(self) -> float:
        return self.timeouts[-1] if self.timeouts else 3.0


def vicp_frame(flag: int, payload: bytes) -> bytes:
    header = struct.pack("B3BI", flag, 1, 0, 0, socket.htonl(len(payload)))
    return header + payload


def attach(scope: LeCroy, sock: FragmentingFakeSocket) -> FragmentingFakeSocket:
    """Give ``scope`` an already-connected fake socket (no hardware I/O)."""
    scope._transport.attach_socket(sock)
    return sock


def sent_commands(sock: FragmentingFakeSocket) -> list[bytes]:
    """Split everything the fake socket received back into command payloads."""
    sent, commands, pos = bytes(sock.sent), [], 0
    while pos < len(sent):
        length = struct.unpack("!I", sent[pos + 4 : pos + 8])[0]
        commands.append(sent[pos + 8 : pos + 8 + length])
        pos += 8 + length
    return commands


# -- LeCroy wiring on top of the transport -----------------------------------------------------


class _ConnectableSocket(FragmentingFakeSocket):
    """Connects instantly (or times out), recording the timeouts applied."""

    def __init__(self, *args, fail_connect: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_connect = fail_connect
        self.connected_to = None
        self.closed = False

    def setsockopt(self, *args) -> None:
        pass

    def connect(self, address) -> None:
        if self.fail_connect:
            raise TimeoutError("timed out")
        self.connected_to = address

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, [LeCroy.MAX_TCP_CONNECT, LeCroy.MAC_TCP_READ]),
        ({"delayval": 7.0, "connect_timeout": 1.0}, [1.0, 7.0]),
    ],
)
def test_connect_bounds_the_handshake_then_the_ongoing_io(monkeypatch, kwargs, expected):
    fake = _ConnectableSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake)

    scope = LeCroy()
    scope.connect("10.0.0.1", **kwargs)

    assert scope.connected and scope.address == "10.0.0.1"
    assert fake.connected_to == ("10.0.0.1", LeCroy.LECROY_SERVER_PORT)
    # handshake bound first, then the bound every later send()/recv() inherits
    assert fake.timeouts == expected

    scope.disconnect()
    assert fake.closed and not scope.connected and scope.address is None


def test_connect_timeout_is_a_lecroy_timeout(monkeypatch):
    fake = _ConnectableSocket(fail_connect=True)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: fake)

    with pytest.raises(LeCroyTimeoutError, match=rf"within {LeCroy.MAX_TCP_CONNECT}s"):
        LeCroy().connect("10.0.0.1")


def test_a_silent_scope_raises_lecroy_timeout_and_drops_the_connection():
    class Silent(FragmentingFakeSocket):
        def recv(self, n: int) -> bytes:
            raise TimeoutError("timed out")

    scope = LeCroy()
    attach(scope, Silent())

    with pytest.raises(LeCroyTimeoutError):
        scope.query("C1:VOLT_DIV?")

    assert scope.connected is False


def test_idn_asks_for_the_identity_and_can_widen_the_timeout():
    scope = LeCroy()
    sock = attach(scope, FragmentingFakeSocket(vicp_frame(0x81, b"LECROY,WS452,1,9.0\n")))

    assert scope.idn(timeout=15.0) == "LECROY,WS452,1,9.0"

    assert sent_commands(sock) == [b"*IDN?"]
    assert sock.timeouts == [15.0, LeCroy.MAC_TCP_READ]  # widened, then restored


def test_read_all_reassembles_fragmented_vicp_frames():
    scope = LeCroy()
    attach(
        scope, FragmentingFakeSocket(vicp_frame(0x80, b"hello ") + vicp_frame(0x01, b"world"), 2)
    )

    assert scope.readAll() == (0x01, "hello world")


# -- waveform download -----------------------------------------------------------------------


def test_get_data_bytes_reads_a_variable_length_definite_block():
    scope = LeCroy()
    response = (
        vicp_frame(0x80, b"C1:WF DAT1,#9")
        + vicp_frame(0x80, b"000000003")
        + vicp_frame(0x80, bytes([0, 1, 255]))
        + vicp_frame(0x81, b"\n")
    )
    sock = attach(scope, FragmentingFakeSocket(response, chunk_size=2))

    result = scope.getDataBytes(channel="C1", block="DAT1")

    assert result == [(0,), (1,), (-1,)]
    # the format is set before the waveform is requested
    assert sent_commands(sock) == [b"CFMT DEF9,BYTE,BIN", b"C1:WF? DAT1"]


def test_get_data_words_reads_definite_block_data_and_validates_word_size():
    scope = LeCroy()
    data = struct.pack("<2h", -123, 456)
    response = (
        vicp_frame(0x80, b"C1:WF DAT1,#9000000004")
        + vicp_frame(0x80, data[:1])
        + vicp_frame(0x80, data[1:])
        + vicp_frame(0x81, b"\n")
    )
    sock = attach(scope, FragmentingFakeSocket(response, chunk_size=2))

    result = scope.getDataWords(channel="C1", block="DAT1")

    assert result == (-123, 456)
    # format and byte order are set before the waveform is requested
    assert sent_commands(sock) == [b"CFMT DEF9,WORD,BIN", b"CORD LO", b"C1:WF? DAT1"]


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
    attach(scope, FragmentingFakeSocket(responses, chunk_size=2))

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
    attach(scope, FragmentingFakeSocket(responses, chunk_size=2))

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
    attach(scope, FragmentingFakeSocket(responses, chunk_size=2))

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
    sock = attach(scope, FragmentingFakeSocket(b""))

    call(scope)

    assert sent_message(sock) == expected


def test_query_sends_the_command_and_returns_the_trimmed_response():
    scope = LeCroy()
    sock = attach(scope, FragmentingFakeSocket(vicp_frame(0x01, b"C1:COUPLING D50 \n")))

    response = scope.query("C1:COUPLING?")

    assert sent_message(sock) == "C1:COUPLING?"
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
    sock = attach(scope, FragmentingFakeSocket(vicp_frame(0x01, response)))

    result = call(scope)

    assert sent_message(sock) == command
    assert result == expected


# -- internal word read decodes straight to an int16 array -------------------------------------


def test_internal_word_read_returns_native_int16_array_without_python_tuples():
    body = struct.pack("<3h", -32768, 0, 32767)
    scope = LeCroy()
    attach(
        scope,
        FragmentingFakeSocket(
            vicp_frame(0x80, b"C1:WF DAT1,#9000000006")
            + vicp_frame(0x80, body)
            + vicp_frame(0x81, b"\n"),
            4096,
        ),
    )

    codes = scope._read_words("C1", "DAT1")

    assert codes.dtype == np.int16
    assert codes.tolist() == [-32768, 0, 32767]
    assert codes.flags.writeable


# -- command strings are validated before they reach the instrument ----------------------------


@pytest.mark.parametrize("bad", ["C1:VOLT_DIV 1;C2", "C1\n"])
def test_free_form_channel_names_must_be_plain_identifiers(bad):
    scope = LeCroy()
    sock = attach(scope, FragmentingFakeSocket())

    with pytest.raises(ValueError, match="invalid channel"):
        scope.set_trace_display(bad, True)
    with pytest.raises(ValueError, match="invalid channel"):
        scope.getDataWords(channel=bad)

    assert not sock.sent, "nothing may be written for an invalid name"


@pytest.mark.parametrize("bad", ["C1;C2", "a\nb"])
def test_math_equation_rejects_quote_and_control_characters(bad):
    scope = LeCroy()
    sock = attach(scope, FragmentingFakeSocket())

    with pytest.raises(ValueError, match="invalid math equation"):
        scope.set_math_equation(MathChannel.F1, bad)

    assert not sock.sent


def test_numeric_setters_reject_non_finite_values():
    scope = LeCroy()
    sock = attach(scope, FragmentingFakeSocket())

    for call in (
        lambda: scope.set_volts_per_div(Channel.C1, float("nan")),
        lambda: scope.set_offset(Channel.C1, float("nan")),
        lambda: scope.set_trigger_level(Channel.C1, float("nan")),
        lambda: scope.set_trigger_delay(float("nan")),
    ):
        with pytest.raises(ValueError, match="must be finite"):
            call()

    assert not sock.sent
