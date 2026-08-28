from enum import IntEnum, StrEnum

import pyvisa
from numpy import load


class CH(IntEnum):
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4


class IP(StrEnum):
    POWER_SUPPLY = "10.140.1.42"
    NOISE_ANALYZER = "10.140.1.115"
    SCOPE = "10.140.1.28"


class TestDevice:
    """Small VISA-compatible test double for development without hardware."""

    def query(self, text: str):
        if text.startswith(":TRACe:DATA? TRACE"):
            return ",".join(str(x) for x in self.get_trace_data())
        print("QUERY?", text)
        return "<device id m00x>"

    def write(self, text: str):
        print("WRITE!", text)

    def close(self):
        pass

    def get_trace_data(self):
        with open("test_data.np", "rb") as data_file:
            return load(data_file)


class BaseDevice:
    """Common VISA connection handling for laboratory instruments."""

    def __init__(self, ip: IP | None = None):
        self.ip = ip
        if ip is None:
            print("No address provided -> TEST MODE")
            self.instrument = TestDevice()
            return

        self.rm = pyvisa.ResourceManager()
        self.instrument = self.open()
        self.instrument.timeout = 10_000

    def open(self):
        return self.rm.open_resource(f"TCPIP0::{self.ip}::inst0::INSTR")

    def close(self) -> None:
        self.instrument.close()
