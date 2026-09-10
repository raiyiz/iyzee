"""LeCroy oscilloscope control and waveform acquisition."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .base import IP
from .vicp import VICP_DATA_FLAG, VICPTransport


@dataclass(frozen=True)
class Waveform:
    """One measured oscilloscope waveform with its physical axes."""

    x: np.ndarray
    y: np.ndarray
    x_unit: str
    y_unit: str
    channel: str


class LeCroy:
    """Remote control and waveform acquisition for a LeCroy oscilloscope."""

    LECROY_SERVER_PORT = 1861
    CMD_BUF_LEN = 8192
    LECROY_EOI_FLAG = 0x01
    LECROY_DATA_FLAG = VICP_DATA_FLAG

    def __init__(
        self,
        ip: IP | str = IP.SCOPE,
        *,
        timeout_s: float = 5.0,
        transport: VICPTransport | None = None,
    ) -> None:
        self.ip = str(ip)
        self.timeout_s = timeout_s
        self.transport = transport or VICPTransport(
            self.ip,
            port=self.LECROY_SERVER_PORT,
            timeout_s=timeout_s,
        )

    @property
    def connected(self) -> bool:
        return self.transport.connected

    def connect(self) -> None:
        """Connect to the scope; repeated calls are harmless."""
        self.transport.connect()

    def close(self) -> None:
        """Close the network connection."""
        self.transport.close()

    def disconnect(self) -> None:
        """Backward-compatible alias for ``close``."""
        self.close()

    def __enter__(self) -> "LeCroy":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("LeCroy scope is not connected")

    def send(self, message: str) -> None:
        """Send an ASCII VICP command."""
        self._require_connected()
        self.transport.send_ascii(message)

    def readAll(self) -> tuple[int, str]:
        """Read a complete framed ASCII response."""
        self._require_connected()
        return self.transport.receive_ascii()

    def identify(self) -> str:
        """Query the scope identification string."""
        self.send("*IDN?")
        _flags, response = self.readAll()
        return response

    def _read_waveform_block(self, *, sample_width: int) -> bytes:
        """Read the scope's waveform response and return raw sample bytes."""
        if sample_width <= 0:
            raise ValueError("sample_width must be positive")
        preamble = self.transport.receive_exact(38)
        if preamble[-11:-9] != b"#9":
            raise RuntimeError("incorrectly returned waveform header")
        try:
            expected_bytes = int(preamble[-9:].decode("ascii"))
        except ValueError as exc:
            raise RuntimeError("invalid waveform byte-count header") from exc
        if expected_bytes <= 0:
            raise RuntimeError(f"invalid waveform byte count: {expected_bytes}")
        if expected_bytes % sample_width:
            raise RuntimeError(
                f"waveform byte count {expected_bytes} is not divisible by sample width {sample_width}"
            )

        data = bytearray()
        while True:
            frame = self.transport.receive_frame()
            if frame.flags & VICP_DATA_FLAG:
                data.extend(frame.payload)
                continue
            if frame.payload != b"\n":
                raise RuntimeError("waveform transfer did not terminate with newline")
            break

        if len(data) != expected_bytes:
            raise RuntimeError(f"expected {expected_bytes} waveform bytes, got {len(data)}")
        return bytes(data)

    def getDataBytes(self, channel: str = "C1", block: str = "DAT1") -> list[tuple[int]]:
        """Return raw signed 8-bit waveform samples."""
        self.send("CFMT DEF9,BYTE,BIN")
        self.send(f"{channel}:WF? {block}")
        raw = self._read_waveform_block(sample_width=1)
        return list(struct.iter_unpack("b", raw))

    def getDataWords(self, channel: str = "C1", block: str = "DAT1") -> tuple[int, ...]:
        """Return raw signed 16-bit little-endian waveform samples."""
        self.send("CFMT DEF9,WORD,BIN")
        self.send(f"{channel}:WF? {block}")
        self.send("CORD LO")
        raw = self._read_waveform_block(sample_width=2)
        return struct.unpack(f"<{len(raw) // 2}h", raw)

    def _inspect(self, channel: str, field: str) -> str:
        self.send(f'{channel}:INSPECT? "{field}"')
        _flags, response = self.readAll()
        return response

    def getDataFloats(self, channel: str = "C1", block: str = "DAT1") -> tuple[str, np.ndarray]:
        """Return physically-scaled vertical samples and their unit."""
        word_values = np.asarray(self.getDataWords(channel=channel, block=block), dtype=np.float64)
        offset = float(self._inspect(channel, "VERTICAL_OFFSET").split(":")[-1].split('"\n')[0].strip())
        gain = float(self._inspect(channel, "VERTICAL_GAIN").split(":")[-1].split('"\n')[0].strip())
        unit = self._inspect(channel, "VERTUNIT").split("Unit Name = ")[-1].split('"\n')[0]
        return unit, gain * word_values - offset

    def getHorProperties(self, channel: str = "C1") -> tuple[str, float, float]:
        """Return horizontal unit, offset, and sample interval."""
        unit = self._inspect(channel, "HORUNIT").split("Unit Name = ")[-1].split('"\n')[0]
        offset = float(self._inspect(channel, "HORIZ_OFFSET").split(":")[-1].split('"\n')[0].strip())
        interval = float(self._inspect(channel, "HORIZ_INTERVAL").split(":")[-1].split('"\n')[0].strip())
        return unit, offset, interval

    def acquire_waveform(self, channel: str = "C1", block: str = "DAT1") -> Waveform:
        """Download and scale one waveform into physical x/y arrays."""
        y_unit, y = self.getDataFloats(channel=channel, block=block)
        x_unit, x_offset, x_interval = self.getHorProperties(channel=channel)
        x = x_offset + x_interval * np.arange(y.size, dtype=np.float64)
        return Waveform(x=x, y=y, x_unit=x_unit, y_unit=y_unit, channel=channel)
