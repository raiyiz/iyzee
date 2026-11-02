"""Client for the WS-7 wavemeter server's small HTTP API.

Wavemeter is a small stateless HTTP client: constructing it does not connect
to anything, and every method call is one ordinary requests call.
The client is used directly from scripts and the console.
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


# Scaling is a bit tricky here, since we span several orders of magnitude, but
# want to avoid huge numbers, which would be the case, if we go straight for SI
# units. So we define the base like the wavelengthmeter as THz.
THz = 1
GHz = 1e-3
MHz = 1e-6

# Rubidium transition frequencies in vacuum as reference (D.Steck), THz


D1_center_85 = 377.107385690 * THz
D2_center_85 = 384.23040637 * THz

D1_center_87 = 377.1074635 * THz
D2_center_87 = 384.2304844685 * THz


Rb_transitions: list[tuple[str, float]] = [
    ("D1 - Rb85_F22", D1_center_85 + (1.770843922 - 0.210923) * GHz),
    ("D1 - Rb85_F23", D1_center_85 + (1.770843922 + 0.150659) * GHz),
    ("D1 - Rb85_F32", D1_center_85 + (-1.264888516 - 0.210923) * GHz),
    ("D1 - Rb85_F33", D1_center_85 + (-1.264888516 + 0.150659) * GHz),
    ("D1 - Rb87_F11", D1_center_87 + (4.27167663181519 - 0.510410) * GHz),
    ("D1 - Rb87_F12", D1_center_87 + (4.27167663181519 + 0.306246) * GHz),
    ("D1 - Rb87_F21", D1_center_87 + (-2.5630059790891 - 0.510410) * GHz),
    ("D1 - Rb87_F22", D1_center_87 + (-2.5630059790891 + 0.306246) * GHz),
    ("D2 - Rb85_F21", D2_center_85 + (1.770843922 - 0.113307) * GHz),
    ("D2 - Rb85_F22", D2_center_85 + (1.770843922 - 0.083955) * GHz),
    ("D2 - Rb85_F23", D2_center_85 + (1.770843922 - 0.020503) * GHz),
    ("D2 - Rb85_F32", D2_center_85 + (-1.264888516 - 0.083955) * GHz),
    ("D2 - Rb85_F33", D2_center_85 + (-1.264888516 - 0.020503) * GHz),
    ("D2 - Rb85_F34", D2_center_85 + (-1.264888516 + 0.100357) * GHz),
    ("D2 - Rb87_F10", D2_center_87 + (4.27167663181519 - 0.3020738) * GHz),
    ("D2 - Rb87_F11", D2_center_87 + (4.27167663181519 - 0.2298518) * GHz),
    ("D2 - Rb87_F12", D2_center_87 + (4.27167663181519 - 0.0729113) * GHz),
    ("D2 - Rb87_F21", D2_center_87 + (-2.5630059790891 - 0.2298518) * GHz),
    ("D2 - Rb87_F22", D2_center_87 + (-2.5630059790891 - 0.0729113) * GHz),
    ("D2 - Rb87_F23", D2_center_87 + (-2.5630059790891 + 0.1937408) * GHz),
    ("Rb85_D1_center", D1_center_85),
    ("Rb87_D1_center", D1_center_87),
    ("Rb85_D2_center", D2_center_85),
    ("Rb87_D2_center", D2_center_87),
]


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


def monitoring_frequencies(channels):
    from tabulate import tabulate  # lazy: only this printout needs it

    channels = list(channels)
    header = ["Transition"] + ["Frequencies (THz)"] + [f"Detuning (ch{c}) / GHz" for c in channels]
    rows = []
    wavemeter = Wavemeter()
    freqs = [wavemeter.read_frequency(c) for c in channels]

    rows.append(["absolute freq (THz)", "", *freqs])
    for label, f in Rb_transitions:
        row = [label, f] + [(freq - f) * 1e3 for freq in freqs]
        rows.append(row)

    print(tabulate(rows, headers=header, tablefmt="psql", floatfmt="+.7f"))


def main():
    """Run the wavemeter frequency monitoring utility."""
    monitoring_frequencies([0, 1])


if __name__ == "__main__":
    main()
