"""Small, testable transport for LeCroy's VICP protocol."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

VICP_HEADER_SIZE = 8
VICP_DATA_FLAG = 0x80
VICP_EOI_FLAG = 0x01


class SocketLike(Protocol):
    """Subset of socket methods required by the VICP transport."""

    def connect(self, address: tuple[str, int]) -> None: ...

    def close(self) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def settimeout(self, value: float | None) -> None: ...


@dataclass(frozen=True)
class VICPFrame:
    """One VICP frame after its 8-byte header has been decoded."""

    flags: int
    payload: bytes


class VICPTransport:
    """Reliable framed transport for LeCroy VICP over TCP."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 1861,
        timeout_s: float = 5.0,
        socket_factory=socket.socket,
        max_frame_size: int = 64 * 1024 * 1024,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._socket_factory = socket_factory
        self.max_frame_size = max_frame_size
        self._socket: SocketLike | None = None

    @property
    def connected(self) -> bool:
        """Whether the transport currently owns an open socket."""
        return self._socket is not None

    @staticmethod
    def _recv_exact(sock: SocketLike, size: int) -> bytes:
        """Receive exactly ``size`` bytes or raise if the peer closes."""
        if size < 0:
            raise ValueError("size must be non-negative")
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError(f"socket closed after {len(data)}/{size} bytes")
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def encode_header(flags: int, payload_size: int) -> bytes:
        """Encode a network-order VICP header."""
        if not 0 <= flags <= 0xFF:
            raise ValueError("VICP flags must fit in one byte")
        if payload_size < 0 or payload_size > 0xFFFFFFFF:
            raise ValueError("VICP payload size must fit in an unsigned 32-bit field")
        return struct.pack("!B3xI", flags, payload_size)

    @staticmethod
    def decode_header(data: bytes) -> tuple[int, int]:
        """Decode an 8-byte network-order VICP header."""
        if len(data) != VICP_HEADER_SIZE:
            raise ValueError(f"VICP header must be {VICP_HEADER_SIZE} bytes")
        flags, size = struct.unpack("!B3xI", data)
        return flags, size

    def connect(self) -> None:
        """Open the TCP connection; repeated calls are harmless."""
        if self.connected:
            return
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_s)
        try:
            sock.connect((self.host, self.port))
        except Exception:
            sock.close()
            raise
        self._socket = sock

    def close(self) -> None:
        """Close the socket, ignoring an already-closed transport."""
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()

    def __enter__(self) -> "VICPTransport":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def _require_socket(self) -> SocketLike:
        if self._socket is None:
            raise RuntimeError("VICP transport is not connected")
        return self._socket

    def send(self, payload: bytes, *, flags: int = VICP_DATA_FLAG | VICP_EOI_FLAG) -> None:
        """Send one complete VICP frame."""
        sock = self._require_socket()
        sock.sendall(self.encode_header(flags, len(payload)) + payload)

    def send_ascii(self, message: str, *, flags: int = VICP_DATA_FLAG | VICP_EOI_FLAG) -> None:
        """Encode and send an ASCII instrument command."""
        self.send(message.encode("ascii"), flags=flags)

    def receive_frame(self) -> VICPFrame:
        """Receive and validate one VICP frame."""
        sock = self._require_socket()
        flags, size = self.decode_header(self._recv_exact(sock, VICP_HEADER_SIZE))
        if size > self.max_frame_size:
            raise ValueError(f"VICP frame of {size} bytes exceeds configured limit")
        return VICPFrame(flags=flags, payload=self._recv_exact(sock, size))

    def receive_message(self) -> tuple[int, bytes]:
        """Receive frames until the VICP data-continuation flag clears."""
        frames: list[bytes] = []
        final_flags = VICP_DATA_FLAG
        while True:
            frame = self.receive_frame()
            frames.append(frame.payload)
            final_flags = frame.flags
            if not (frame.flags & VICP_DATA_FLAG):
                return final_flags, b"".join(frames)

    def receive_ascii(self) -> tuple[int, str]:
        """Receive a framed ASCII response."""
        flags, payload = self.receive_message()
        return flags, payload.decode("ascii")
