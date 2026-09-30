from __future__ import annotations

import logging
import socket
import struct
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

log = logging.getLogger("iyzee.vicp")

VICP_EOI_FLAG = 0x01
VICP_DATA_FLAG = 0x80

# A DEF9 block is followed by a line terminator that is never sample data.
_TERMINATORS = (b"\n", b"\r\n")


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


def _close_socket(sock: object) -> None:
    """Close a socket-like object; fakes without ``close()`` are tolerated."""
    close = getattr(sock, "close", None)
    if close is not None:
        close()


def recv_exact(
    sock: object,
    num_bytes: int,
    *,
    timeout_error: type[TimeoutError] = VICPTimeoutError,
) -> bytes:
    """Read exactly the requested number of bytes from a socket-like object."""
    if num_bytes < 0:
        raise ValueError(f"num_bytes must be non-negative, got {num_bytes}")

    recv = getattr(sock, "recv", None)
    if recv is None:
        raise TypeError("socket-like object must provide recv()")

    chunks = bytearray()
    gettimeout = getattr(sock, "gettimeout", lambda: None)
    while len(chunks) < num_bytes:
        try:
            chunk = recv(num_bytes - len(chunks))
        except TimeoutError as exc:
            raise timeout_error(
                f"no response after {gettimeout()}s ({len(chunks)}/{num_bytes} bytes received)"
            ) from exc
        if not chunk:
            raise ConnectionError(f"Socket closed after {len(chunks)}/{num_bytes} bytes")
        chunks.extend(chunk)
    return bytes(chunks)


def send_all(sock: object, data: bytes, *, timeout_error: type[TimeoutError]) -> None:
    """Write all bytes, tolerating partial socket.send() results."""
    send = getattr(sock, "send", None)
    if send is None:
        raise TypeError("socket-like object must provide send()")

    gettimeout = getattr(sock, "gettimeout", lambda: None)
    sent = 0
    while sent < len(data):
        try:
            count = send(data[sent:])
        except TimeoutError as exc:
            raise timeout_error(
                f"no response after {gettimeout()}s ({sent}/{len(data)} bytes sent)"
            ) from exc
        if count is None:
            raise ConnectionError("socket send() returned None")
        if count <= 0:
            raise ConnectionError(f"socket send() made no progress ({sent}/{len(data)} bytes sent)")
        sent += count


class _MessageState:
    """Tracks whether the current response has been consumed through EOI."""

    complete = False


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

    HEADER_SIZE = 8
    # LeCroy's VICP header version is encoded as 0x02 ("version 1").
    # This is a wire value, not the human-facing version number.
    HEADER_VERSION = 0x02
    DEFAULT_PORT = 1861
    DEFAULT_CONNECT_TIMEOUT = 5.0
    DEFAULT_IO_TIMEOUT = 3.0
    MAX_MESSAGE_FRAMES = 65_536

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
        self._socket: object | None = None
        self._address: str | None = None
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    @property
    def address(self) -> str | None:
        return self._address

    @property
    def socket(self) -> object | None:
        return self._socket

    @property
    def transaction_lock(self) -> threading.RLock:
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize one complete logical operation on the connection."""
        with self._lock:
            yield

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
    def _message(self) -> Iterator[_MessageState]:
        """Guard one response: invalidate if it fails before EOI was consumed."""
        state = _MessageState()
        try:
            yield state
        except BaseException as exc:
            if not state.complete:
                self._invalidate(exc)
            raise

    def _require_socket(self) -> object:
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
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    log.debug("could not set TCP_NODELAY on VICP socket", exc_info=True)
            except TimeoutError as exc:
                sock.close()
                raise self._timeout_error(
                    f"no response connecting to {address}:{self.port} within {connect_timeout}s"
                ) from exc
            except OSError:
                sock.close()
                raise

            self._socket = sock
            self._address = address
            self.connect_timeout = connect_timeout
            self.io_timeout = io_timeout

    def close(self) -> None:
        """Close the connection and clear all observable connection state."""
        with self._lock:
            sock = self._socket
            self._socket = None
            self._address = None
            if sock is None:
                return
            _close_socket(sock)

    def attach_socket(self, sock: object) -> None:
        """Attach a socket-like object for tests and legacy LeCroy.s use."""
        with self._lock:
            if self._socket is not None:
                raise RuntimeError("Already connected")
            self._socket = sock
            self._address = None

    def _write_frame(self, payload: bytes) -> None:
        sock = self._require_socket()
        header = struct.pack(
            "!4BI",
            VICP_DATA_FLAG | VICP_EOI_FLAG,
            self.HEADER_VERSION,
            0,
            0,
            len(payload),
        )
        try:
            send_all(sock, header + payload, timeout_error=self._timeout_error)
        except BaseException as exc:
            self._invalidate(exc)
            raise

    def send_command(self, message: str) -> None:
        """Send one ASCII VICP command as one EOI-terminated frame."""
        payload = message.encode("ascii")
        if len(payload) > self.max_command_length:
            raise ValueError(
                f"command is {len(payload)} bytes; maximum is {self.max_command_length}"
            )
        with self._lock:
            self._write_frame(payload)

    def read_frame(self) -> VICPFrame:
        with self._lock:
            return self._read_frame()

    def _read_frame(self) -> VICPFrame:
        sock = self._require_socket()
        try:
            header = recv_exact(sock, self.HEADER_SIZE, timeout_error=self._timeout_error)
            flags, version, _reserved_1, _reserved_2, length = struct.unpack("!4BI", header)
            if version != self.HEADER_VERSION:
                raise VICPProtocolError(
                    f"unsupported VICP header version {version}; expected {self.HEADER_VERSION}"
                )
            payload = recv_exact(sock, length, timeout_error=self._timeout_error)
        except BaseException as exc:
            # Mid-frame failure: the stream position is unknown.
            self._invalidate(exc)
            raise
        return VICPFrame(flags=flags, payload=payload)

    def read_message(self) -> tuple[int, bytes]:
        """Read frames through EOI and return the final flags plus complete payload."""
        with self._lock, self._message() as state:
            chunks = bytearray()
            for _ in range(self.MAX_MESSAGE_FRAMES):
                frame = self._read_frame()
                chunks.extend(frame.payload)
                if frame.is_eoi:
                    state.complete = True
                    return frame.flags, bytes(chunks)
            raise VICPProtocolError(
                f"message exceeded {self.MAX_MESSAGE_FRAMES} VICP frames without EOI"
            )

    def read_definite_block(self) -> bytes:
        """Read an IEEE 488.2 definite-length binary block through VICP EOI.

        LeCroy DEF9 waveform responses place an ASCII #9 marker and a
        nine-digit byte count before the binary payload. The marker and count
        may span VICP frames, so parsing must not depend on a fixed prefix.

        The count is authoritative. A final EOI frame that holds only a line
        terminator is never treated as sample data, so a truncated block cannot
        be "completed" by its own terminator. Errors raised once EOI has been
        consumed (wrong length, bad trailer) leave the connection usable; errors
        before EOI invalidate it (see the class docstring).
        """
        with self._lock, self._message() as state:
            header = bytearray()
            data = bytearray()
            expected: int | None = None
            trailing = bytearray()

            for _ in range(self.MAX_MESSAGE_FRAMES):
                frame = self._read_frame()
                state.complete = frame.is_eoi
                payload = frame.payload if frame.is_data else b""

                if expected is None:
                    header.extend(payload)
                    payload = b""
                    marker_index = header.find(b"#9")
                    if marker_index >= 0:
                        count_start = marker_index + 2
                        count_end = count_start + 9
                        if len(header) < count_end:
                            if frame.is_eoi:
                                raise VICPProtocolError(
                                    "VICP definite-length block ended inside its length header"
                                )
                            continue

                        count_field = bytes(header[count_start:count_end])
                        if not count_field.isdigit():
                            raise VICPProtocolError(f"invalid DEF9 byte count {count_field!r}")
                        expected = int(count_field)
                        # Whatever followed the count in this frame is body, and
                        # goes through the same length/terminator rules below.
                        payload = bytes(header[count_end:])
                        header.clear()

                if expected is not None and payload:
                    remaining = expected - len(data)
                    if frame.is_eoi and remaining > 0 and payload in _TERMINATORS:
                        trailing.extend(payload)
                    elif remaining > 0:
                        take = min(remaining, len(payload))
                        data.extend(payload[:take])
                        trailing.extend(payload[take:])
                    else:
                        trailing.extend(payload)

                if frame.is_eoi:
                    if expected is None:
                        raise VICPProtocolError(
                            "VICP binary response reached EOI without a DEF9 block"
                        )
                    if len(data) != expected:
                        raise VICPProtocolError(f"Expected {expected} bytes, got {len(data)}")
                    if bytes(trailing) not in (b"", *_TERMINATORS):
                        raise VICPProtocolError(
                            f"unexpected bytes after DEF9 block: {bytes(trailing)!r}"
                        )
                    return bytes(data)

            raise VICPProtocolError(
                f"binary response exceeded {self.MAX_MESSAGE_FRAMES} VICP frames without EOI"
            )

    def query(self, message: str) -> str:
        """Send one command and atomically read its complete ASCII response."""
        payload = message.encode("ascii")
        if len(payload) > self.max_command_length:
            raise ValueError(
                f"command is {len(payload)} bytes; maximum is {self.max_command_length}"
            )
        with self._lock:
            self._write_frame(payload)
            _flag, response = self.read_message()
            try:
                return response.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                # The whole response was consumed, so the stream is still aligned.
                raise VICPProtocolError("VICP text response was not valid ASCII") from exc
