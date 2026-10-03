"""Tests for the VICP transport: framing, DEF9 blocks and the failure contract.

The transport has no request IDs, so after any mid-message failure a late
reply would be handed to the next caller as its own answer. These tests pin
down that the transport invalidates itself in that case, stays usable when the
stream is still aligned, and never lets a line terminator stand in for samples.
Driver-level behaviour (commands, parsing) lives in ``test_scope.py``.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from iyzee.vicp import (
    VICP_DATA_FLAG,
    VICP_EOI_FLAG,
    VICPFrame,
    VICPProtocolError,
    VICPTimeoutError,
    VICPTransport,
    recv_exact,
)

DATA = VICP_DATA_FLAG
EOI = VICP_EOI_FLAG
DATA_EOI = DATA | EOI


def frame(flags: int, payload: bytes, version: int = 1) -> bytes:
    return struct.pack("!4BI", flags, version, 0, 0, len(payload)) + payload


def block_header(count: int, prefix: bytes = b"C1:WF DAT1,") -> bytes:
    return prefix + b"#9" + f"{count:09d}".encode()


class ScriptedSocket:
    """recv() serves ``data`` in ``chunk``-byte pieces, then times out."""

    def __init__(self, data: bytes = b"", chunk: int = 4096, timeout: float = 3.0):
        self.buf = bytearray(data)
        self.chunk = chunk
        self.sent = bytearray()
        self.closed = False
        self.timeouts: list[float] = []
        self._timeout = timeout

    def gettimeout(self) -> float:
        return self._timeout

    def settimeout(self, value: float) -> None:
        self._timeout = value
        self.timeouts.append(value)

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


def attached(data: bytes = b"", **kwargs) -> tuple[VICPTransport, ScriptedSocket]:
    transport = VICPTransport()
    sock = ScriptedSocket(data, **kwargs)
    transport.attach_socket(sock)
    return transport, sock


# -- recv_exact ------------------------------------------------------------------------------


def test_recv_exact_reassembles_fragmented_reads():
    assert recv_exact(ScriptedSocket(b"0123456789ABCDEF", chunk=3), 16) == b"0123456789ABCDEF"


def test_recv_exact_raises_on_closed_connection():
    class Closed(ScriptedSocket):
        def recv(self, n: int) -> bytes:
            return b""

    with pytest.raises(ConnectionError):
        recv_exact(Closed(), 100)


@pytest.mark.parametrize("got", [b"", b"abcd"])
def test_recv_exact_timeout_reports_how_far_it_got(got):
    with pytest.raises(VICPTimeoutError, match=rf"3\.0s \({len(got)}/10 bytes received\)"):
        recv_exact(ScriptedSocket(got, chunk=2), 10)


# -- transactions ----------------------------------------------------------------------------


def test_transaction_is_reentrant():
    transport = VICPTransport()

    with transport.transaction():
        with transport.transaction():
            assert transport.connected is False


def test_transactions_are_serialized():
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

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    threads[0].start()
    assert first_entered.wait(2.0)
    threads[1].start()

    assert not second_entered.wait(0.05)
    release_first.set()
    for thread in threads:
        thread.join(timeout=2.0)

    assert second_entered.is_set()


# -- writing ---------------------------------------------------------------------------------


def test_send_command_is_one_eoi_data_frame():
    transport, sock = attached()

    transport.send_command("C1:VDIV 1.0")

    assert bytes(sock.sent) == frame(DATA_EOI, b"C1:VDIV 1.0")


def test_send_command_survives_partial_sends():
    class Partial(ScriptedSocket):
        def send(self, data: bytes) -> int:
            return super().send(data[:2])

    transport = VICPTransport()
    sock = Partial()
    transport.attach_socket(sock)

    transport.send_command("C1:TEST")

    assert bytes(sock.sent) == frame(DATA_EOI, b"C1:TEST")


def test_zero_progress_send_is_an_error_and_invalidates():
    class Stalled(ScriptedSocket):
        def send(self, data: bytes) -> int:
            return 0

    transport = VICPTransport()
    transport.attach_socket(Stalled())

    with pytest.raises(ConnectionError, match="no progress"):
        transport.send_command("C1:TEST")

    assert transport.connected is False


def test_stalled_write_times_out_and_invalidates():
    class TimesOut(ScriptedSocket):
        def send(self, data: bytes) -> int:
            raise TimeoutError("timed out")

    transport = VICPTransport()
    transport.attach_socket(TimesOut(timeout=2.0))

    with pytest.raises(VICPTimeoutError, match=r"2\.0s \(0/\d+ bytes sent\)"):
        transport.send_command("C1:VDIV 1.0")

    assert transport.connected is False


# -- a failed read invalidates the connection -----------------------------------------------


def test_timeout_invalidates_the_connection_and_closes_the_socket():
    transport, sock = attached()

    with pytest.raises(VICPTimeoutError):
        transport.query("C1:VOLT_DIV?")

    assert transport.connected is False
    assert transport.address is None
    assert sock.closed


def test_late_reply_is_never_returned_to_the_next_caller():
    """The desync scenario: q1 times out, its reply shows up afterwards, and
    q2 must not receive it as its own answer."""
    transport, sock = attached()
    with pytest.raises(VICPTimeoutError):
        transport.query("C1:VOLT_DIV?")

    sock.buf += frame(DATA_EOI, b"C1:VOLT_DIV 5.00E-01 V\n")  # the late reply

    with pytest.raises(ConnectionError, match="not connected"):
        transport.query("C2:OFFSET?")


def test_peer_close_invalidates_the_connection():
    class Closed(ScriptedSocket):
        def recv(self, n: int) -> bytes:
            return b""

    transport = VICPTransport()
    transport.attach_socket(Closed())

    with pytest.raises(ConnectionError):
        transport.query("*IDN?")

    assert transport.connected is False


def test_unsupported_header_version_invalidates_the_connection():
    transport, _ = attached(frame(DATA_EOI, b"x", version=2))

    with pytest.raises(VICPProtocolError, match="unsupported VICP header version 2"):
        transport.query("*IDN?")

    assert transport.connected is False


def test_frame_cap_without_eoi_invalidates(monkeypatch):
    monkeypatch.setattr(VICPTransport, "MAX_MESSAGE_FRAMES", 3)
    transport, _ = attached(frame(DATA, b"a") * 5)

    with pytest.raises(VICPProtocolError, match="without EOI"):
        transport.read_message()

    assert transport.connected is False


def test_real_socket_timeout_invalidates_the_transport():
    near, far = socket.socketpair()
    try:
        near.settimeout(0.05)
        transport = VICPTransport()
        transport.attach_socket(near)

        with pytest.raises(VICPTimeoutError):
            transport.query("*IDN?")

        assert transport.connected is False
    finally:
        near.close()
        far.close()


# -- a scope that accepts the connection but is slow or silent on the first query ------------


def test_first_query_gets_a_widened_timeout_that_is_restored_afterwards():
    transport, sock = attached(frame(DATA_EOI, b"LECROY,WS452\n") + frame(DATA_EOI, b"NORM\n"))

    assert transport.query("*IDN?", timeout=15.0) == "LECROY,WS452"
    assert transport.query("TRIG_MODE?") == "NORM"

    # widened for the first exchange, put back before the second
    assert sock.timeouts == [15.0, transport.io_timeout]


def test_a_silent_first_query_reports_the_widened_bound_and_leaves_a_clean_slate(monkeypatch):
    transport, sock = attached(timeout=3.0)

    with pytest.raises(VICPTimeoutError, match=r"15\.0s \(0/8 bytes received\)"):
        transport.query("*IDN?", timeout=15.0)

    assert transport.connected is False and sock.closed

    fresh = ScriptedSocket(frame(DATA_EOI, b"LECROY,WS452\n"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Connectable(fresh))
    transport.connect("10.0.0.9")  # the retry path the connect screen takes

    assert transport.query("*IDN?") == "LECROY,WS452"


def test_a_timeout_override_must_be_positive():
    transport, sock = attached(frame(DATA_EOI, b"x\n"))

    with pytest.raises(ValueError, match="positive"):
        transport.query("*IDN?", timeout=0)

    assert transport.connected is True
    assert not sock.sent


# -- connect / close -------------------------------------------------------------------------


class _Connectable:
    """Connects instantly and then behaves like ``target``."""

    def __init__(self, target: ScriptedSocket) -> None:
        self._target = target

    def setsockopt(self, *args) -> None:
        pass

    def connect(self, address) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._target, name)


class _ConnectTimeoutSocket(ScriptedSocket):
    """A TCP handshake that never completes (scope off, or a firewall drop)."""

    def connect(self, address) -> None:
        raise TimeoutError("timed out")

    def setsockopt(self, *args) -> None:
        pass


def test_failed_connect_is_bounded_closes_the_socket_and_publishes_nothing(monkeypatch):
    half_open = _ConnectTimeoutSocket()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: half_open)
    transport = VICPTransport()

    with pytest.raises(VICPTimeoutError, match=rf"within {transport.connect_timeout}s"):
        transport.connect("10.0.0.1")

    assert half_open.timeouts == [transport.connect_timeout]  # handshake bound, nothing else
    assert half_open.closed
    assert not transport.connected
    assert transport.address is None


def test_connect_while_connected_raises():
    transport, _ = attached()

    with pytest.raises(RuntimeError, match="Already connected"):
        transport.connect("10.0.0.1")


def test_close_shuts_down_then_closes_clears_state_and_is_idempotent():
    events: list[str] = []

    class Recording(ScriptedSocket):
        def shutdown(self, how: int) -> None:
            events.append(f"shutdown:{how}")

        def close(self) -> None:
            events.append("close")

    transport = VICPTransport()
    transport.attach_socket(Recording())

    transport.close()
    transport.close()

    # shutdown first so the scope sees a FIN and frees its session
    assert events == [f"shutdown:{socket.SHUT_RDWR}", "close"]
    assert transport.connected is False
    assert transport.address is None


def _listener() -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server


def test_connect_sets_tcp_nodelay_so_back_to_back_commands_are_not_delayed():
    server = _listener()
    try:
        transport = VICPTransport(port=server.getsockname()[1])
        transport.connect("127.0.0.1")
        try:
            sock = transport.socket
            assert isinstance(sock, socket.socket)
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
        finally:
            transport.close()
    finally:
        server.close()


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


# -- messages and frames ---------------------------------------------------------------------


def test_read_frame_reassembles_a_fragmented_frame():
    transport, _ = attached(frame(DATA_EOI, b"hello"), chunk=2)

    got = transport.read_frame()

    assert isinstance(got, VICPFrame)
    assert got.is_data and got.is_eoi
    assert got.payload == b"hello"


def test_read_message_joins_frames_through_eoi():
    transport, _ = attached(frame(DATA, b"hello ") + frame(EOI, b"world"), chunk=2)

    assert transport.read_message() == (EOI, b"hello world")


def test_wrong_block_length_after_eoi_keeps_the_connection_usable():
    transport, _ = attached(
        frame(DATA, block_header(4))
        + frame(DATA, b"\x01\x02")
        + frame(EOI, b"\n")
        + frame(DATA_EOI, b"NEXT\n")
    )

    with pytest.raises(VICPProtocolError, match="Expected 4 bytes, got 2"):
        transport.read_definite_block()

    assert transport.connected is True
    assert transport.read_message() == (DATA_EOI, b"NEXT\n")


def test_non_ascii_reply_keeps_the_connection_usable():
    transport, _ = attached(frame(DATA_EOI, b"\xff\xfe") + frame(DATA_EOI, b"OK\n"))

    with pytest.raises(VICPProtocolError, match="not valid ASCII"):
        transport.query("*IDN?")

    assert transport.connected is True
    assert transport.query("*OPC?") == "OK"


def test_bad_count_mid_message_keeps_the_connection_usable_because_the_message_is_fully_read():
    transport, _ = attached(
        frame(DATA, b"C1:WF DAT1,#9notanumber")
        + frame(DATA_EOI, b"more")
        + frame(DATA_EOI, b"OK\n")
    )

    with pytest.raises(VICPProtocolError, match="invalid DEF9 byte count"):
        transport.read_definite_block()

    assert transport.connected is True
    assert transport.query("*OPC?") == "OK"


def test_bad_count_in_the_final_frame_keeps_the_connection_usable():
    transport, _ = attached(frame(DATA_EOI, b"C1:WF DAT1,#9notanumber"))

    with pytest.raises(VICPProtocolError, match="invalid DEF9 byte count"):
        transport.read_definite_block()

    assert transport.connected is True


# -- the terminator is never sample data -----------------------------------------------------


def test_terminator_frame_cannot_complete_a_short_block():
    """3 of 4 bytes + a "\\n" frame used to come back as 4 'samples'."""
    transport, _ = attached(
        frame(DATA, block_header(4)) + frame(DATA, b"\x01\x02\x03") + frame(DATA_EOI, b"\n")
    )

    with pytest.raises(VICPProtocolError, match="Expected 4 bytes, got 3"):
        transport.read_definite_block()


def test_sample_value_0x0a_is_kept_when_the_terminator_follows_separately():
    body = b"\x01\x0a"
    transport, _ = attached(frame(DATA, block_header(2)) + frame(DATA, body) + frame(EOI, b"\n"))

    assert transport.read_definite_block() == body


def test_binary_body_containing_the_block_marker_is_not_reparsed():
    body = b"#9\n#9123456789"
    transport, _ = attached(
        frame(DATA, block_header(len(body))) + frame(DATA, body) + frame(EOI, b"\n")
    )

    assert transport.read_definite_block() == body


def test_bytes_beyond_the_declared_length_are_rejected():
    transport, _ = attached(frame(DATA_EOI, block_header(2) + b"\x01\x02EXTRA"))

    with pytest.raises(VICPProtocolError, match="unexpected bytes"):
        transport.read_definite_block()


@pytest.mark.parametrize("terminator", [frame(EOI, b"\n"), frame(DATA_EOI, b"\n")])
def test_block_ends_with_a_separate_terminator_frame(terminator):
    body = bytes([0, 1, 255])
    transport, _ = attached(frame(DATA, block_header(3)) + frame(DATA, body) + terminator)

    assert transport.read_definite_block() == body


def test_zero_length_block():
    transport, _ = attached(frame(DATA, block_header(0)) + frame(EOI, b"\n"))

    assert transport.read_definite_block() == b""


@pytest.mark.parametrize("chunk", [1, 7])
def test_every_frame_split_point_yields_the_same_block(chunk):
    """Split header + body at every byte (including between '#' and '9', and
    inside the nine-digit count) and read it back through tiny recv()s."""
    body = bytes(range(10, 20))
    stream = block_header(len(body)) + body
    for cut in range(1, len(stream)):
        data = frame(DATA, stream[:cut]) + frame(DATA, stream[cut:]) + frame(EOI, b"\n")
        transport, _ = attached(data, chunk=chunk)

        assert transport.read_definite_block() == body, f"cut at {cut}"


# -- a link that dies while nobody is talking ------------------------------------------------


def _connected_pair() -> tuple[VICPTransport, socket.socket, socket.socket]:
    """A transport attached to one end of a real socket pair; ``far`` is the 'scope'."""
    near, far = socket.socketpair()
    transport = VICPTransport(io_timeout=1.0)
    transport.attach_socket(near)
    return transport, near, far


def test_check_link_is_true_for_a_healthy_idle_connection_and_sends_nothing():
    transport, near, far = _connected_pair()
    try:
        assert transport.check_link() is True
        assert transport.connected
        far.setblocking(False)
        with pytest.raises(BlockingIOError):
            far.recv(1)  # nothing was written to the peer
        assert near.gettimeout() == transport.io_timeout  # blocking mode restored
    finally:
        near.close()
        far.close()


def test_check_link_notices_the_peer_closing_and_invalidates():
    transport, near, far = _connected_pair()
    try:
        far.close()

        assert transport.check_link() is False

        assert transport.connected is False
        with pytest.raises(ConnectionError, match="not connected"):
            transport.query("*IDN?")
    finally:
        near.close()


def test_check_link_does_not_consume_an_unexpected_reply():
    transport, near, far = _connected_pair()
    try:
        far.sendall(frame(DATA_EOI, b"LATE\n"))

        assert transport.check_link() is True  # judged by the next read, not here
        assert transport.read_message() == (DATA_EOI, b"LATE\n")  # still all there
    finally:
        near.close()
        far.close()


def test_check_link_reports_false_when_never_connected():
    assert VICPTransport().check_link() is False


def test_check_link_never_blocks_behind_a_transaction_in_flight():
    transport, near, far = _connected_pair()
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with transport.transaction():
            holding.set()
            release.wait(2.0)

    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert holding.wait(2.0)
        far.close()  # even a dead peer: the transaction in flight reports that itself

        assert transport.check_link() is True
    finally:
        release.set()
        worker.join(2.0)
        near.close()


def test_check_link_tolerates_test_doubles_without_peek_support():
    transport, _ = attached()  # ScriptedSocket.recv() takes no flags

    assert transport.check_link() is True
    assert transport.connected is True


def test_connect_enables_keepalive_so_a_dead_peer_is_noticed():
    server = _listener()
    try:
        transport = VICPTransport(port=server.getsockname()[1])
        transport.connect("127.0.0.1")
        try:
            sock = transport.socket
            assert isinstance(sock, socket.socket)
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
            if hasattr(socket, "TCP_KEEPIDLE"):
                assert (
                    sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE)
                    == VICPTransport.KEEPALIVE_IDLE_S
                )
        finally:
            transport.close()
    finally:
        server.close()


def test_a_failed_shutdown_does_not_stop_the_close():
    class Reset(ScriptedSocket):
        def shutdown(self, how: int) -> None:
            raise OSError("not connected")

    transport = VICPTransport()
    sock = Reset()
    transport.attach_socket(sock)

    transport.close()

    assert sock.closed and not transport.connected
