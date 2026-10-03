from __future__ import annotations

import logging
import socket
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger("iyzee.vicp")

VICP_EOI_FLAG = 0x01
VICP_DATA_FLAG = 0x80

# A DEF9 block is followed by a line terminator that is never sample data.
_TERMINATORS = (b"\n", b"\r\n")
_HEADER = struct.Struct("!4BI")  # flags, version, 2 reserved bytes, payload length


class VICPTimeoutError(TimeoutError):
    """A VICP operation exceeded the configured socket timeout."""


class VICPProtocolError(RuntimeError):
    """A VICP frame or message violated the transport contract."""


@dataclass(frozen=True)
class VICPFrame:
    """One complete VICP frame read from the instrument."""

    flags: int
    payload: bytes

    @property
    def is_data(self) -> bool:
        return bool(self.flags & VICP_DATA_FLAG)

    @property
    def is_eoi(self) -> bool:
        return bool(self.flags & VICP_EOI_FLAG)


def _close_socket(sock: socket.socket) -> None:
    """Shut down, then close.

    ``shutdown()`` first so the peer gets a FIN right away (a bare ``close()``
    can leave the connection half-open while another thread still holds the
    descriptor), which is what lets an instrument that serves one client at a
    time free its session.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # already closed or never fully connected
    sock.close()


def _tune_socket(sock: socket.socket, idle: int, interval: int, count: int) -> None:
    """Disable Nagle and have the OS probe an idle link (dead peer noticed in ~``idle + interval * count`` s).

    Without keepalive a cable pull, a VPN drop or a sleeping laptop leaves the
    socket looking healthy until the next command times out. Option names differ
    by platform (macOS spells the idle option ``TCP_KEEPALIVE``), so each is
    applied only where it exists; failures are logged, not fatal.
    """
    options = [
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
    ]
    for name, value in (
        ("TCP_KEEPIDLE", idle),
        ("TCP_KEEPALIVE", idle),
        ("TCP_KEEPINTVL", interval),
        ("TCP_KEEPCNT", count),
    ):
        option = getattr(socket, name, None)
        if option is not None:
            options.append((socket.IPPROTO_TCP, option, value))
    for level, option, value in options:
        try:
            sock.setsockopt(level, option, value)
        except OSError:
            log.debug("could not set socket option %s on VICP socket", option, exc_info=True)


def _recv_exact(
    sock: Any, num_bytes: int, timeout_error: type[TimeoutError] = VICPTimeoutError
) -> bytes:
    """Read exactly ``num_bytes`` from ``sock``."""
    chunks = bytearray()
    while len(chunks) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(chunks))
        except TimeoutError as exc:
            raise timeout_error(
                f"no response after {sock.gettimeout()}s ({len(chunks)}/{num_bytes} bytes received)"
            ) from exc
        if not chunk:
            raise ConnectionError(f"Socket closed after {len(chunks)}/{num_bytes} bytes")
        chunks.extend(chunk)
    return bytes(chunks)


def _send_all(sock: Any, data: bytes, timeout_error: type[TimeoutError]) -> None:
    """Write all of ``data``, tolerating partial ``send()`` results."""
    sent = 0
    while sent < len(data):
        try:
            count = sock.send(data[sent:])
        except TimeoutError as exc:
            raise timeout_error(
                f"no response after {sock.gettimeout()}s ({sent}/{len(data)} bytes sent)"
            ) from exc
        if not count or count <= 0:
            raise ConnectionError(f"socket send() made no progress ({sent}/{len(data)} bytes sent)")
        sent += count


class VICPTransport:
    """Thread-safe VICP/TCP transport.

    The transport owns connection state and serializes complete logical
    request/response transactions. The lock is re-entrant so high-level
    operations can safely compose send, read, and query primitives.

    Failure contract: VICP has no request IDs, so once a read or write fails
    part-way (timeout, peer close, bad header, an interrupted transfer) the
    byte stream can no longer be trusted, and a late reply would be handed to
    the *next* caller as if it answered *its* question. The transport therefore
    invalidates itself: the socket is closed, ``connected`` becomes ``False``
    and the caller must reconnect. Errors raised after a response was read
    completely through EOI (e.g. a bad block length) leave the stream aligned,
    so the connection stays usable.
    """

    HEADER_VERSION = 1
    DEFAULT_PORT = 1861
    DEFAULT_CONNECT_TIMEOUT = 5.0
    DEFAULT_IO_TIMEOUT = 3.0
    MAX_MESSAGE_FRAMES = 65_536
    # TCP keepalive: probe after 10 s idle, every 5 s, give up after 3 misses.
    KEEPALIVE_IDLE_S = 10
    KEEPALIVE_INTERVAL_S = 5
    KEEPALIVE_COUNT = 3

    def __init__(
        self,
        *,
        port: int = DEFAULT_PORT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        io_timeout: float = DEFAULT_IO_TIMEOUT,
        max_command_length: int = 8192,
        timeout_error: type[TimeoutError] = VICPTimeoutError,
    ) -> None:
        self.port = port
        self.connect_timeout = connect_timeout
        self.io_timeout = io_timeout
        self.max_command_length = max_command_length
        self._timeout_error = timeout_error
        self._socket: socket.socket | None = None
        self._address: str | None = None
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def address(self) -> str | None:
        return self._address

    @property
    def transaction_lock(self) -> threading.RLock:
        return self._lock

    def _invalidate(self, reason: BaseException | str | None = None) -> None:
        """Drop the connection because the byte stream is no longer trustworthy."""
        with self._lock:
            sock = self._socket
            self._socket = None
            self._address = None
        if sock is None:
            return
        log.warning("VICP connection dropped after failure: %s", reason or "unknown")
        try:
            _close_socket(sock)
        except OSError:
            pass

    @contextmanager
    def _io_timeout(self, timeout: float | None) -> Iterator[None]:
        """Apply ``timeout`` to the live socket for one transaction, then restore it.

        The first reply after connecting can be much slower than steady state,
        and a timeout is fatal to the stream (see the class docstring), so the
        caller that knows it is waiting on a slow answer widens the bound
        instead of failing and reconnecting.
        """
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")
        sock = self._socket
        if timeout is None or sock is None:
            yield
            return
        sock.settimeout(timeout)
        try:
            yield
        finally:
            if self._socket is sock:  # not invalidated meanwhile
                sock.settimeout(self.io_timeout)

    def _require_socket(self) -> socket.socket:
        sock = self._socket
        if sock is None:
            raise ConnectionError("VICP transport is not connected")
        return sock

    def connect(
        self,
        address: str,
        *,
        connect_timeout: float | None = None,
        io_timeout: float | None = None,
    ) -> None:
        """Open a VICP connection and publish it only after both setup steps succeed."""
        connect_timeout = self.connect_timeout if connect_timeout is None else connect_timeout
        io_timeout = self.io_timeout if io_timeout is None else io_timeout
        if connect_timeout <= 0 or io_timeout <= 0:
            raise ValueError("connect_timeout and io_timeout must be positive")

        with self._lock:
            if self._socket is not None:
                raise RuntimeError("Already connected")

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(connect_timeout)
            try:
                sock.connect((address, self.port))
                sock.settimeout(io_timeout)
                _tune_socket(
                    sock, self.KEEPALIVE_IDLE_S, self.KEEPALIVE_INTERVAL_S, self.KEEPALIVE_COUNT
                )
            except OSError as exc:
                sock.close()
                if isinstance(exc, TimeoutError):
                    raise self._timeout_error(
                        f"no response connecting to {address}:{self.port} within {connect_timeout}s"
                    ) from exc
                raise

            self._socket = sock
            self._address = address
            self.connect_timeout = connect_timeout
            self.io_timeout = io_timeout
            log.info(
                "VICP connected to %s:%d (local port %s)", address, self.port, sock.getsockname()[1]
            )

    def close(self) -> None:
        """Close the connection and clear all observable connection state."""
        with self._lock:
            sock = self._socket
            self._socket = None
            self._address = None
            if sock is None:
                return
            _close_socket(sock)

    def attach_socket(self, sock: Any) -> None:
        """Attach an already-connected socket-like object (used by tests)."""
        with self._lock:
            if self._socket is not None:
                raise RuntimeError("Already connected")
            self._socket = sock
            self._address = None

    def check_link(self) -> bool:
        """Is the connection still usable? Sends nothing; never blocks.

        Catches a link that died while idle: the peer closed it, or keepalive
        gave up on it. A dead link is invalidated exactly as if a command had
        failed, so ``connected`` turns ``False`` and callers see one state.

        If a transaction is in flight the answer is "yes": that transaction
        owns the stream and will report its own failure.
        """
        sock = self._socket
        if sock is None:
            return False
        if not self._lock.acquire(blocking=False):
            return True
        try:
            if self._socket is not sock:
                return self._socket is not None
            sock.settimeout(0)
            try:
                peeked = sock.recv(1, socket.MSG_PEEK)
            except BlockingIOError, InterruptedError:
                return True  # nothing waiting: healthy and idle
            except OSError as exc:
                self._invalidate(exc)
                return False
            if peeked == b"":
                self._invalidate("peer closed the connection")
                return False
            return True  # unexpected bytes: leave judging them to the next read
        finally:
            if self._socket is sock:
                sock.settimeout(self.io_timeout)
            self._lock.release()

    def _write_frame(self, payload: bytes) -> None:
        sock = self._require_socket()
        header = _HEADER.pack(
            VICP_DATA_FLAG | VICP_EOI_FLAG, self.HEADER_VERSION, 0, 0, len(payload)
        )
        try:
            _send_all(sock, header + payload, self._timeout_error)
        except BaseException as exc:
            self._invalidate(exc)
            raise

    def _encode(self, message: str) -> bytes:
        payload = message.encode("ascii")
        if len(payload) > self.max_command_length:
            raise ValueError(
                f"command is {len(payload)} bytes; maximum is {self.max_command_length}"
            )
        return payload

    def send_command(self, message: str) -> None:
        """Send one ASCII VICP command as one EOI-terminated frame."""
        payload = self._encode(message)
        with self._lock:
            self._write_frame(payload)

    def _read_frame(self) -> VICPFrame:
        sock = self._require_socket()
        try:
            header = _recv_exact(sock, _HEADER.size, timeout_error=self._timeout_error)
            flags, version, _reserved_1, _reserved_2, length = _HEADER.unpack(header)
            if version != self.HEADER_VERSION:
                raise VICPProtocolError(
                    f"unsupported VICP header version {version}; expected {self.HEADER_VERSION}"
                )
            payload = _recv_exact(sock, length, timeout_error=self._timeout_error)
        except BaseException as exc:
            # Mid-frame failure: the stream position is unknown.
            self._invalidate(exc)
            raise
        return VICPFrame(flags=flags, payload=payload)

    def _read_frames(self) -> list[VICPFrame]:
        """Read frames through EOI (the caller holds the lock).

        Any failure here leaves the stream position unknown, so the connection
        is dropped (``_read_frame`` already does that for I/O errors).
        """
        frames: list[VICPFrame] = []
        for _ in range(self.MAX_MESSAGE_FRAMES):
            frame = self._read_frame()
            frames.append(frame)
            if frame.is_eoi:
                return frames
        error = VICPProtocolError(
            f"message exceeded {self.MAX_MESSAGE_FRAMES} VICP frames without EOI"
        )
        self._invalidate(error)
        raise error

    def read_message(self) -> tuple[int, bytes]:
        """Read frames through EOI and return the final flags plus complete payload."""
        with self._lock:
            frames = self._read_frames()
        return frames[-1].flags, b"".join(f.payload for f in frames)

    def read_definite_block(self) -> bytes:
        """Read an IEEE 488.2 definite-length binary block through VICP EOI.

        LeCroy DEF9 waveform responses place an ASCII ``#9`` marker and a
        nine-digit byte count before the binary payload; both may span VICP
        frames, so the whole message is read through EOI before it is parsed.
        That keeps the stream aligned: every parse error below leaves the
        connection usable (only a failure *before* EOI invalidates it; see the
        class docstring).

        The count is authoritative. A final EOI frame holding only a line
        terminator is the trailer, never sample data, so a truncated block
        cannot be "completed" by its own terminator.
        """
        with self._lock:
            frames = self._read_frames()
        payloads = [f.payload for f in frames if f.is_data]
        trailer = payloads.pop() if payloads and payloads[-1] in _TERMINATORS else b""
        raw = b"".join(payloads)

        marker = raw.find(b"#9")
        if marker < 0:
            raise VICPProtocolError("VICP binary response reached EOI without a DEF9 block")
        count_field = raw[marker + 2 : marker + 11]
        if len(count_field) < 9:
            raise VICPProtocolError("VICP definite-length block ended inside its length header")
        if not count_field.isdigit():
            raise VICPProtocolError(f"invalid DEF9 byte count {count_field!r}")
        expected = int(count_field)

        body = raw[marker + 11 :]
        if len(body) < expected:
            raise VICPProtocolError(f"Expected {expected} bytes, got {len(body)}")
        extra = body[expected:] + trailer
        if extra not in (b"", *_TERMINATORS):
            raise VICPProtocolError(f"unexpected bytes after DEF9 block: {extra!r}")
        return body[:expected]

    def query(self, message: str, *, timeout: float | None = None) -> str:
        """Send one command and atomically read its complete ASCII response.

        ``timeout`` replaces the I/O timeout for this exchange only.
        """
        payload = self._encode(message)
        with self._lock, self._io_timeout(timeout):
            self._write_frame(payload)
            _flag, response = self.read_message()
            try:
                return response.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                # The whole response was consumed, so the stream is still aligned.
                raise VICPProtocolError("VICP text response was not valid ASCII") from exc
