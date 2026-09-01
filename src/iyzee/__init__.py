"""iyzee: laboratory instrument control and measurement procedures."""

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

    def __init__(self, ip: IP | None = None):
        self.ip = ip
        self.rm = pyvisa.ResourceManager()
        self.instrument = self.open()
        self.instrument.timeout = 10_000

    def open(self):
        return self.rm.open_resource(f"TCPIP0::{self.ip}::inst0::INSTR")

    def close(self) -> None:
        self.instrument.close()


__all__ = ["BaseDevice", "CH", "IP"]
