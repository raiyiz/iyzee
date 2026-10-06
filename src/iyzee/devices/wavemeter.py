"""Client for the WS-7 wavemeter server's small HTTP API.

Wavemeter is a small stateless HTTP client: constructing it does not connect
to anything, and every method call is one ordinary requests call.
The module-level functions are convenience calls on a default client, for
scripts and experiment procedures.
"""

from __future__ import annotations

import logging
import time

import numpy as np
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
            f"{self.base_url}{channel}/",
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


def track_frequency(total_time, time_step, save_path, channel=DEFAULT_CHANNEL, reference_f=0):
    """Live-plot one channel for `total_time` s, then save the plot and a CSV to `save_path`.

    A failed read skips that sample (logged with the raw error) instead of
    ending a long run and losing the data collected so far.
    """
    # Plotting and the CSV export are only needed here. Imported lazily because
    # this module is also imported for its HTTP client by the TUI, where
    # pandas alone was a fifth of the startup time.
    import matplotlib.pyplot as plt
    import pandas as pd

    # Initialize numpy arrays for time and frequency data
    times = np.array([])
    track_freq = np.array([])

    # Turn on interactive mode for live plotting
    plt.ion()
    _fig, ax = plt.subplots(figsize=(4.5, 2.5))

    (_line,) = ax.plot([], [], "b-", label="Laser Frequency (THz)")
    ax.fill_between([], [], [], color="blue", alpha=0.3)

    wavemeter = Wavemeter()
    start_time = time.time()

    # Function to update the plot with new data
    def update_plot(ls_frequency: float) -> None:
        nonlocal times, track_freq

        # Calculate the elapsed time since the start of data collection
        elapsed_time = time.time() - start_time

        # Append new data to numpy arrays
        times = np.append(times, elapsed_time)
        track_freq = np.append(track_freq, ls_frequency)

        ax.clear()
        ax.plot(times, track_freq, "b-", label="Laser Frequency (THz)")
        ax.fill_between(times, track_freq, color="blue", alpha=0.3)

        ax.relim()  # Recalculate limits
        ax.autoscale_view()  # Rescale the plot

        ax.text(
            times[-1],
            track_freq[-1],
            f"{track_freq[-1]} THz",
            fontsize=12,
            ha="right",
            va="bottom",
            color="red",
        )

        ax.set_title(f"Live Plot of Laser Frequency CH{channel}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Frequency (THz)")
        ax.legend(loc="upper left")
        plt.pause(0.01)

    while time.time() - start_time <= total_time:
        try:
            ls_frequency = wavemeter.read_frequency(channel) - reference_f
        except (requests.RequestException, OSError, ValueError) as exc:
            log.warning("[WS-7] ch%s: sample skipped: %r", channel, exc)
        else:
            update_plot(ls_frequency)
        time.sleep(time_step)

    plt.savefig(save_path + "/laser_frequency_plot.pdf", bbox_inches="tight", dpi=1500)
    plt.savefig(save_path + "/laser_frequency_plot.svg", bbox_inches="tight", dpi=1500)

    # Save data to CSV file using pandas
    data = pd.DataFrame({"tracking_time_s": times, "laser_frequency_thz": track_freq})
    data.to_csv(save_path + "/laser_frequency_readout.csv", index=False)

    print(f"Laser Frequency data saved to {save_path}.")
    print("Mean Frequency (THz):", np.mean(track_freq))
    print("Sigma Frequency (THz):", np.std(track_freq))
    plt.ioff()
    plt.show()

    return times, track_freq


def monitoring_frequencies(channels):
    from tabulate import tabulate  # lazy: see track_frequency

    channels = list(channels)
    header = ["Transition"] + ["Frequencies (THz)"] + [f"Detuning (ch{c}) / GHz" for c in channels]
    rows = []
    wavemeter = Wavemeter()
    freqs = [wavemeter.read_frequency(c) for c in channels]

    rows.append(["absolute freq (THz)"] + [""] + list(freqs))
    for label, f in Rb_transitions:
        row = [label, f] + [(freq - f) * 1e3 for freq in freqs]
        rows.append(row)

    print(tabulate(rows, headers=header, tablefmt="psql", floatfmt="+.7f"))


def main():
    """Run the wavemeter frequency monitoring utility."""
    monitoring_frequencies([0, 1])


if __name__ == "__main__":
    main()
