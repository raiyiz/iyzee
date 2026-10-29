"""Shared device infrastructure for laboratory instruments."""

from typing import Any

import pyvisa

from ..config import IP

SHUTTER_CHANNEL_DEFAULT = 3


class BaseDevice:
    """Common VISA connection handling for laboratory instruments."""

    def __init__(
        self,
        ip: IP | str | None = None,
        resource_manager=None,
        timeout_ms: int = 10_000,
        read_termination: str | None = None,
        write_termination: str | None = None,
    ):
        self.ip = ip
        self.timeout_ms = timeout_ms
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.rm = resource_manager or pyvisa.ResourceManager()
        self.instrument: Any = None
        self.visa_address = f"TCPIP0::{self.ip}::inst0::INSTR"

    def open(self):
        return self.rm.open_resource(self.visa_address)

    def connect(self) -> None:
        """Open the device once; repeated calls reuse the existing resource."""
        if self.instrument is not None:
            return
        self.instrument = self.open()
        self.instrument.timeout = self.timeout_ms
        if self.read_termination is not None:
            self.instrument.read_termination = self.read_termination
        if self.write_termination is not None:
            self.instrument.write_termination = self.write_termination

    def close(self) -> None:
        """Close the VISA resource, if it is open."""
        if self.instrument is not None:
            self.instrument.close()
            self.instrument = None

    def __enter__(self):
        """Open the device at the explicit resource boundary."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the VISA resource when leaving the context."""
        self.close()
        return False


__all__ = ["IP", "BaseDevice"]
