"""Shared device infrastructure for laboratory instruments."""

from enum import IntEnum, StrEnum

import pyvisa


class CH(IntEnum):
    """Power-supply channel identifiers."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4


class IP(StrEnum):
    """IP addresses of the laboratory instruments."""

    POWER_SUPPLY = "10.140.1.42"
    NOISE_ANALYZER = "10.140.1.115"
    SCOPE = "10.140.1.28"
    WAVEMETER = "10.140.1.118"


class BaseDevice:
    """Common VISA connection handling for laboratory instruments."""

    def __init__(self, ip: IP | None = None, resource_manager=None):
        self.ip = ip
        self.rm = resource_manager or pyvisa.ResourceManager()
        self.instrument = None
        self.connect()

    def open(self):
        return self.rm.open_resource(f"TCPIP0::{self.ip}::inst0::INSTR")

    def connect(self) -> None:
        """Open the device once; repeated calls reuse the existing resource."""
        if self.instrument is not None:
            return
        self.instrument = self.open()
        self.instrument.timeout = 10_000

    def close(self) -> None:
        """Close the VISA resource, if it is open."""
        if self.instrument is not None:
            self.instrument.close()
            self.instrument = None

    def __enter__(self):
        """Return an open device for use in a context manager."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close the VISA resource when leaving the context."""
        self.close()
        return False


__all__ = ["CH", "IP", "BaseDevice"]
