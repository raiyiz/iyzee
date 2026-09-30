"""Failure-contract tests for the VICP transport and the DEF9 block reader.

The transport has no request IDs, so after any mid-message failure a late
reply would be handed to the next caller as its own answer. These tests pin
down that the transport invalidates itself in that case, stays usable when the
stream is still aligned, and never lets a line terminator stand in for samples.
"""

from __future__ import annotations

import socket
import struct

import pytest

from iyzee.scope import LeCroy, LeCroyTimeoutError
from iyzee.vicp import VICPProtocolError, VICPTransport

DATA = 0x80
EOI = 0x01
DATA_EOI = DATA | EOI

def frame(flags: int, payload: bytes, version: int = 1) -> bytes:
    return struct.pack("!4BI", flags, version, 0, 0, len(payload)) + payload

def block_header(count: int, prefix: bytes = b"C1:WF DAT1,") -> bytes:
    return prefix + b"#9" + f"{count:09d}".encode()

def sent_commands(sent: bytes) -> list[bytes]:
    """Split everything a fake socket received back into command payloads."""
    commands, pos = [], 0
    while pos < len(sent):
        length = struct.unpack("!I", sent[pos + 4 : pos + 8])[0]
        commands.append(sent[pos + 8 : pos + 8 + length])
        pos += 8 + length
    return commands

class ScriptedSocket:
    """recv() serves ``data`` in ``chunk``-byte pieces, then times out."""

    def __init__(self, data: bytes = b"", chunk: int = 4096, timeout: float = 3.0):
        self.buf = bytearray(data)
        self.chunk = chunk
        self.sent = bytearray()
        self.closed = False
        self._timeout = timeout

    def gettimeout(self) -> float:
        return self._timeout

    def recv(self, n: int) -> bytes:
        if not self.buf:
            raise TimeoutError("timed out")
        take = min(n, self.chunk, len(self.buf))
        chunk = bytes(self.buf[:take])
        del self.buf[:take]
        return chunk

    def send(self, data: bytes) -> int:
        self.sent.extend(data)
        return len(data)

    def close(self) -> None:
        self.closed = True

def attached(data: bytes = b"", **kwargs) -> tuple[LeCroy, ScriptedSocket]:
    scope = LeCroy()
    sock = ScriptedSocket(data, **kwargs)
    scope.s = sock
    return scope, sock

# -- a failed read/write invalidates the connection -----------------------------------------

def test_timeout_invalidates_the_connection_and_closes_the_socket():
    scope, sock = attached()

    with pytest.raises(LeCroyTimeoutError):
        scope.query("C1:VOLT_DIV?")

    assert scope.connected is False
    assert scope.address is None
    assert sock.closed

def test_late_reply_is_never_returned_to_the_next_caller():
    """The desync scenario: q1 times out, its reply shows up afterwards, and
    q2 must not receive it as its own answer."""
    scope, sock = attached()
    with pytest.raises(LeCroyTimeoutError):
        scope.query("C1:VOLT_DIV?")

    sock.buf += frame(DATA_EOI, b"C1:VOLT_DIV 5.00E-01 V\n")  # the late reply

    with pytest.raises(ConnectionError, match="not connected"):
        scope.query("C2:OFFSET?")

def test_peer_close_invalidates_the_connection():
    class ClosedSocket(ScriptedSocket):
        def recv(self, n: int) -> bytes:
            return b""

    scope = LeCroy()
    scope.s = ClosedSocket()

    with pytest.raises(ConnectionError):
        scope.query("*IDN?")

    assert scope.connected is False

def test_unsupported_header_version_invalidates_the_connection():
    scope, sock = attached(frame(DATA_EOI, b"x", version=2))

    with pytest.raises(VICPProtocolError, match="header version"):
        scope.query("*IDN?")

    assert scope.connected is False

def test_stalled_write_invalidates_the_connection():
    class StalledSend(ScriptedSocket):
        def send(self, data: bytes) -> int:
            raise TimeoutError("timed out")

    scope = LeCroy()
    scope.s = StalledSend()

    with pytest.raises(LeCroyTimeoutError):
        scope.send("C1:VDIV 1.0")

    assert scope.connected is False

def test_a_dropped_connection_can_be_re_established(monkeypatch):
    """Invalidation must leave the transport in a state where connect() works."""
    scope, _sock = attached()
    with pytest.raises(LeCroyTimeoutError):
        scope.query("*IDN?")

    fresh = ScriptedSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Connectable(fresh))
    scope.connect("10.0.0.9")

    assert scope.connected is True
    assert scope.address == "10.0.0.9"

class _Connectable:
    def __init__(self, target: ScriptedSocket) -> None:
        self._target = target

    def settimeout(self, value) -> None:
        pass

    def setsockopt(self, *args) -> None:
        pass

    def connect(self, address) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._target, name)

# -- same contract with real sockets (real socket.timeout, real EOF) -----------------------

def test_real_socket_timeout_invalidates_the_transport():
    near, far = socket.socketpair()
    try:
        near.settimeout(0.05)
        transport = VICPTransport(timeout_error=LeCroyTimeoutError)
        transport.attach_socket(near)

        with pytest.raises(LeCroyTimeoutError):
            transport.query("*IDN?")

        assert transport.connected is False
    finally:
        near.close()
        far.close()

def test_wrong_block_length_after_eoi_keeps_the_connection_usable():
    data = (
        frame(DATA, block_header(4))
        + frame(DATA, b"\x01\x02")
        + frame(EOI, b"\n")
        + frame(DATA_EOI, b"NEXT\n")
    )
    scope, _ = attached(data)

    with pytest.raises(VICPProtocolError, match="Expected 4 bytes, got 2"):
        scope.getDataBytes()

    assert scope.connected is True
    assert scope.readAll() == (DATA_EOI, "NEXT\n")

def test_non_ascii_reply_keeps_the_connection_usable():
    scope, _ = attached(frame(DATA_EOI, b"\xff\xfe") + frame(DATA_EOI, b"OK\n"))

    with pytest.raises(VICPProtocolError, match="not valid ASCII"):
        scope.query("*IDN?")

    assert scope.connected is True
    assert scope.query("*OPC?") == "OK"

def test_bad_count_before_eoi_invalidates_because_frames_are_still_unread():
    scope, _ = attached(frame(DATA, b"C1:WF DAT1,#9notanumber") + frame(DATA_EOI, b"more"))

    with pytest.raises(VICPProtocolError, match="invalid DEF9 byte count"):
        scope.getDataBytes()

    assert scope.connected is False

def test_bad_count_in_the_final_frame_keeps_the_connection_usable():
    scope, _ = attached(frame(DATA_EOI, b"C1:WF DAT1,#9notanumber"))

    with pytest.raises(VICPProtocolError, match="invalid DEF9 byte count"):
        scope.getDataBytes()

    assert scope.connected is True

def test_frame_cap_without_eoi_invalidates(monkeypatch):
    monkeypatch.setattr(VICPTransport, "MAX_MESSAGE_FRAMES", 3)
    scope, _ = attached(frame(DATA, b"a") * 5)

    with pytest.raises(VICPProtocolError, match="without EOI"):
        scope.readAll()

    assert scope.connected is False

# -- the terminator is never sample data ----------------------------------------------------

def test_terminator_frame_cannot_complete_a_short_block():
    """3 of 4 bytes + a "\\n" frame used to come back as 4 'samples'."""
    scope, _ = attached(
        frame(DATA, block_header(4)) + frame(DATA, b"\x01\x02\x03") + frame(DATA_EOI, b"\n")
    )

    with pytest.raises(VICPProtocolError, match="Expected 4 bytes, got 3"):
        scope.getDataBytes()

@pytest.mark.parametrize("terminator", [frame(EOI, b"\n"), frame(DATA_EOI, b"\n")])
def test_block_ends_with_a_separate_terminator_frame(terminator):
    body = bytes([0, 1, 255])
    scope, _ = attached(frame(DATA, block_header(3)) + frame(DATA, body) + terminator)

    assert scope._transport.read_definite_block() == body

def test_get_data_words_sets_format_and_byte_order_before_requesting_the_waveform():
    body = struct.pack("<2h", -123, 456)
    scope, sock = attached(frame(DATA, block_header(4)) + frame(DATA, body) + frame(EOI, b"\n"))

    assert scope.getDataWords(channel="C2", block="DAT1") == (-123, 456)

    assert sent_commands(bytes(sock.sent)) == [
        b"CFMT DEF9,WORD,BIN",
        b"CORD LO",
        b"C2:WF? DAT1",
    ]

def test_get_data_bytes_sets_format_before_requesting_the_waveform():
    scope, sock = attached(frame(DATA, block_header(1)) + frame(DATA, b"\x05") + frame(EOI, b"\n"))

    scope.getDataBytes(channel="C1", block="DAT1")

    assert sent_commands(bytes(sock.sent)) == [b"CFMT DEF9,BYTE,BIN", b"C1:WF? DAT1"]

# -- connect(): real loopback server -------------------------------------------------------

def _listener() -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server

def test_loopback_round_trip_and_reconnect_after_peer_disconnect():
    server = _listener()
    try:
        transport = VICPTransport(port=server.getsockname()[1], io_timeout=1.0)
        transport.connect("127.0.0.1")
        peer, _ = server.accept()
        peer.sendall(frame(DATA_EOI, b"LECROY,WS452\n"))
        assert transport.query("*IDN?") == "LECROY,WS452"

        peer.close()  # scope goes away
        with pytest.raises(ConnectionError):
            transport.query("*IDN?")
        assert transport.connected is False

        transport.connect("127.0.0.1")  # and we can come back
        peer2, _ = server.accept()
        peer2.sendall(frame(DATA_EOI, b"AGAIN\n"))
        assert transport.query("*IDN?") == "AGAIN"
        peer2.close()
        transport.close()
    finally:
        server.close()

# -- connect()/disconnect() semantics -----------------------------------------------------

def test_connect_while_connected_raises_instead_of_printing_and_returning_minus_two():
    scope, _ = attached()

    with pytest.raises(RuntimeError, match="Already connected"):
        scope.connect("10.0.0.1")

def test_disconnect_is_idempotent():
    scope, sock = attached()

    scope.disconnect()
    scope.disconnect()

    assert sock.closed and scope.connected is False

# -- command strings are validated before they reach the instrument -----------------------

@pytest.mark.parametrize("bad", ["C1:VOLT_DIV 1;C2", "C1\n"])
def test_free_form_channel_names_must_be_plain_identifiers(bad):
    scope, sock = attached()

    with pytest.raises(ValueError, match="invalid channel"):
        scope.set_trace_display(bad, True)
    with pytest.raises(ValueError, match="invalid channel"):
        scope.getDataWords(channel=bad)

    assert not sock.sent, "nothing may be written for an invalid name"

@pytest.mark.parametrize("bad", ["C1;C2", "a\nb"])
def test_math_equation_rejects_quote_and_control_characters(bad):
    from iyzee.scope import MathChannel

    scope, sock = attached()

    with pytest.raises(ValueError, match="invalid math equation"):
        scope.set_math_equation(MathChannel.F1, bad)

    assert not sock.sent

@pytest.mark.parametrize("value", [float("nan")])
def test_numeric_setters_reject_non_finite_values(value):
    from iyzee.scope import Channel

    scope, sock = attached()

    for call in (
        lambda: scope.set_volts_per_div(Channel.C1, value),
        lambda: scope.set_offset(Channel.C1, value),
        lambda: scope.set_trigger_level(Channel.C1, value),
        lambda: scope.set_trigger_delay(value),
    ):
        with pytest.raises(ValueError, match="must be finite"):
            call()

    assert not sock.sent

def test_valid_math_equation_is_still_sent_verbatim():
    from iyzee.scope import MathChannel

    scope, sock = attached()

    scope.set_math_equation(MathChannel.F2, "C1-C2")

    assert sent_commands(bytes(sock.sent)) == [b"F2:DEFINE EQN,'C1-C2'"]

# -- word download decodes straight to an int16 array ------------------------------------

def test_internal_word_read_returns_native_int16_array_without_python_tuples():
    import numpy as np

    body = struct.pack("<3h", -32768, 0, 32767)
    scope, _ = attached(frame(DATA, block_header(6)) + frame(DATA, body) + frame(EOI, b"\n"))

    codes = scope._read_words("C1", "DAT1")

    assert codes.dtype == np.int16
    assert codes.tolist() == [-32768, 0, 32767]
    assert codes.flags.writeable
