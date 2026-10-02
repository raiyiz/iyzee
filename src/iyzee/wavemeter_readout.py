import logging
import time
import urllib.request

import numpy as np
import requests

from iyzee import IP

log = logging.getLogger("iyzee.wavemeter")

# Every request to the wavemeter server is bounded: a sweep calls it while it
# holds the instrument locks, so an unanswered request would freeze the run
# (and make quitting wait for it).
READ_TIMEOUT_S = 1.0
SETPOINT_TIMEOUT_S = 3.0


# Scaling is a bit tricky here, since we span several orders of magnitude, but
# want to avoid huge numbers, hwich would be the case, if we go straight for SI
# units. So we define as the base unit here giga hertz and scale accordingly
# up/down from that.
THz = 1e3
GHz = 1
MHz = 1e-3

# Rubidium transition frequencies in vacuum as reference (D.Steck), THz


D1_center_85 = 377.107385690 * THz
D2_center_85 = 384.23040637 * THz

D1_center_87 = 377.1074635 * THz
D2_center_87 = 384.2304844685 * THz



Rb_transitions = [
    ["D1 - Rb85_F22", D1_center_85 + (1.770843922 - 0.210923)  ],
    ["D1 - Rb85_F23", D1_center_85 + (1.770843922 + 0.150659)  ],
    ["D1 - Rb85_F32", D1_center_85 + (-1.264888516 - 0.210923) ],
    ["D1 - Rb85_F33", D1_center_85 + (-1.264888516 + 0.150659) ],
    ["D1 - Rb87_F11", D1_center_87 + (4.27167663181519 - 0.510410) ],
    ["D1 - Rb87_F12", D1_center_87 + (4.27167663181519 + 0.306246) ],
    ["D1 - Rb87_F21", D1_center_87 + (-2.5630059790891 - 0.510410) ],
    ["D1 - Rb87_F22", D1_center_87 + (-2.5630059790891 + 0.306246) ],
    ["D2 - Rb85_F21", D2_center_85 + (1.770843922 - 0.113307)  ],
    ["D2 - Rb85_F22", D2_center_85 + (1.770843922 - 0.083955)  ],
    ["D2 - Rb85_F23", D2_center_85 + (1.770843922 - 0.020503)  ],
    ["D2 - Rb85_F32", D2_center_85 + (-1.264888516 - 0.083955) ],
    ["D2 - Rb85_F33", D2_center_85 + (-1.264888516 - 0.020503) ],
    ["D2 - Rb85_F34", D2_center_85 + (-1.264888516 + 0.100357) ],
    ["D2 - Rb87_F10", D2_center_87 + (4.27167663181519 - 0.3020738) ],
    ["D2 - Rb87_F11", D2_center_87 + (4.27167663181519 - 0.2298518) ],
    ["D2 - Rb87_F12", D2_center_87 + (4.27167663181519 - 0.0729113) ],
    ["D2 - Rb87_F21", D2_center_87 + (-2.5630059790891 - 0.2298518) ],
    ["D2 - Rb87_F22", D2_center_87 + (-2.5630059790891 - 0.0729113) ],
    ["D2 - Rb87_F23", D2_center_87 + (-2.5630059790891 + 0.1937408) ],
    ["Rb85_D1_center", D1_center_85],
    ["Rb87_D1_center", D1_center_87],
    ["Rb85_D2_center", D2_center_85],
    ["Rb87_D2_center", D2_center_87],
]


class WavemeterReadoutError(RuntimeError):
    """Raised when a wavemeter measurement cannot be obtained or parsed."""


class Wavemeter:
    """Client for the WS-7 HTTP API.

    The instrument has no persistent session; each operation is an HTTP request.
    This object owns the default channel so callers can pass an explicit
    wavemeter device around instead of module-level helpers.
    """

    def __init__(self, channel: int = 0) -> None:
        self.channel = channel

    def read_frequency(self, channel: int | None = None) -> float:
        selected = self.channel if channel is None else channel
        try:
            return float(_request(f"{selected}/", timeout=READ_TIMEOUT_S))
        except (OSError, ValueError, UnicodeError) as exc:
            raise WavemeterReadoutError(
                f"Failed to read wavemeter channel {selected}"
            ) from exc

    def set_pid_setpoint(self, freq: float, channel: int | None = None) -> None:
        selected = self.channel if channel is None else channel
        try:
            _request(
                "set_pid/",
                data=f"freq_thz={freq}&channel={selected}".encode("ascii"),
                timeout=SETPOINT_TIMEOUT_S,
            )
        except (OSError, UnicodeError) as exc:
            raise WavemeterReadoutError(
                f"Failed to set the setpoint of wavemeter channel {selected} to {freq} THz"
            ) from exc
        log.info("[WS-7] set PID setpoint of channel %s to %s THz", selected, freq)



def _request(path: str, *, timeout: float, data: bytes | None = None) -> str:
    """One bounded HTTP request to the wavemeter server; returns the decoded body.

    Raises ``OSError`` (including ``HTTPError`` and timeouts), ``UnicodeError``.
    """
    url = f"http://{IP.WAVEMETER}:8000/api/{path}"
    resp = requests.get(url)
    # with urllib.request.urlopen(url, data=data, timeout=timeout) as response:
    #     return response.read().decode("ascii")


def single_readout(
    channel: int, reference_f: float = 0, label: str = "", printing: bool = True
) -> float:
    """Fetch one laser frequency and optionally subtract a reference."""
    ls_frequency = Wavemeter(channel=channel).read_frequency() - reference_f
    if printing:
        print(
            f"[WS-7] Laser Frequency in Channel {channel} (THz) (Ref: {label}): ",
            ls_frequency,
        )
    return ls_frequency


def fast_readout(ch: int) -> float:
    """
    direct urllib request, without try/except
    """
    return float(
        urllib.request.urlopen(f"http://{IP.WAVEMETER}:8000/api/{ch}/", timeout=0.1)
        .read()
        .decode("ascii")
    )


def set_pid_setpoint(freq: float, channel: int) -> None:
    """Set a PID setpoint, retained as a convenience wrapper."""
    Wavemeter(channel=channel).set_pid_setpoint(freq)


def track_frequency(
    total_time, time_step, save_path, channel, reference_f=0, save_csv=False
):
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

    start_time = time.time()

    # Function to update the plot with new data
    def update_plot():
        nonlocal times, track_freq

        # Fetch laser frequency from the URL
        try:  # readout laser frequency and plot laser detuning or absolute laser frequency
            ls_frequency = (
                float(
                    urllib.request.urlopen(
                        f"http://{IP.WAVEMETER}:8000/api/{channel}/", timeout=2
                    )
                    .read()
                    .decode("ascii")
                )
                - reference_f
            )
        except (OSError, ValueError, UnicodeError) as exc:
            print(f"Error fetching data: {exc}")
            return

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
        update_plot()
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


def monitoring_frequencies(channels, two_photon=True):
    from tabulate import tabulate  # lazy: see track_frequency

    header = (
        ["Transition"]
        + ["Frequencies (THz)"]
        + [f"Detuning (ch{c}) / GHz" for c in channels]
    )
    rows = []
    freqs = [single_readout(c, reference_f=0, printing=False) for c in channels]

    rows.append(["absolute freq (THz)"] + [""] + list(freqs))
    for label, f in Rb_transitions:
        row = [label, f] + [(freq - f) * 1e3 for freq in freqs]
        rows.append(row)

    if two_photon:
        delta = compute_two_photon_detuning(f1=freqs[0], f2=freqs[1])
        for label, d in delta:
            row = [label] + [""] + [d * 1e6]  # detuning in MHz
            rows.append(row)

    print(tabulate(rows, headers=header, tablefmt="psql", floatfmt="+.7f"))


def main():
    """Run the wavemeter frequency monitoring utility."""
    monitoring_frequencies([0, 1])


if __name__ == "__main__":
    main()
