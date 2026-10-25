"""Client for the WS-7 wavemeter server's small HTTP API.

Wavemeter is a small stateless HTTP client: constructing it does not connect
to anything, and every method call is one ordinary requests call.
It can be used directly from scripts.
"""

from __future__ import annotations

import logging

import requests

from ..config import IP, address

log = logging.getLogger("iyzee.wavemeter")

# Every request to the wavemeter server is bounded: a sweep calls it while it
# holds the instrument locks, so an unanswered request would freeze the run
# (and make quitting wait for it).
READ_TIMEOUT_S = 1.0
SETPOINT_TIMEOUT_S = 3.0
DEFAULT_CHANNEL = 4
WAVEMETER_PORT = 8000


class Wavemeter:
    """HTTP client for the wavemeter server.

    The client does not hold a connection or any measurement state. The host
    and port only select where the individual HTTP requests are sent.
    """

    def __init__(self, host: str | None = None, port: int = WAVEMETER_PORT) -> None:
        self.host = address(IP.WAVEMETER) if host is None else host
        self.port = port

    @property
    def base_url(self) -> str:
        return f"http://{self.host or address(IP.WAVEMETER)}:{self.port}/api/"

    def __repr__(self) -> str:
        return f"<Wavemeter {self.base_url}>"

    def read_frequency(self, channel: int = DEFAULT_CHANNEL) -> float:
        """Read the frequency of one wavemeter channel in THz."""
        response = requests.get(
            f"{self.base_url}frequency/{channel}/",
            timeout=READ_TIMEOUT_S,
        )
        response.raise_for_status()
        return float(response.text)

    def set_pid_setpoint(self, freq: float, channel: int = DEFAULT_CHANNEL) -> None:
        """Set the PID lock setpoint of one channel, in THz."""
        response = requests.post(
            f"{self.base_url}set_pid/",
            data={"freq_thz": freq, "channel": channel},
            timeout=SETPOINT_TIMEOUT_S,
        )
        response.raise_for_status()
        log.info("[WS-7] set PID setpoint of channel %s to %s THz", channel, freq)
