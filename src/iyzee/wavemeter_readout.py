import time
import urllib.request

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tabulate import tabulate

from iyzee import IP

"""
Readout of single laser frequency with Wavemeter switch over network

Live plotting and single readout of a channel
"""
D1_center_85 = (
    377.107385690  # D1 Rubidium 85 transition frequency vacuum as reference (D.Steck), THz
)
D2_center_85 = 384.230406373  # D2 Rubidium 85 transition frequency, THz

D1_center_87 = 377.1074635  # D1 Rubidium 87 in THz
D2_center_87 = 384.2304844685  # D2

two_photon_762 = 393.37534  # in THz = 762.10282 nm  (vacuum, recalculated from NIST)
two_photon_778 = 385.285142375  # MEP
two_photon_776_52 = 386.3411662603302  # THz, NIST recalculated with ratio of 'two_photon' to its NIST value (n in air).
two_photon_776_32 = 386.25226107557904
# channel = sys.argv[1] # Channel as input

# Rb85 absolute transition frequencies (GHz)
scal = 1e-3

# From Daniel Stecks scripts on Rb 85 & 87..
Rb_transitions = [
    ["D1 - Rb85_F22", D1_center_85 + (1.770843922 - 0.210923) * scal],  # D1 Rb85
    ["D1 - Rb85_F23", D1_center_85 + (1.770843922 + 0.150659) * scal],
    ["D1 - Rb85_F32", D1_center_85 + (-1.264888516 - 0.210923) * scal],
    ["D1 - Rb85_F33", D1_center_85 + (-1.264888516 + 0.150659) * scal],
    ["D1 - Rb87_F11", D1_center_87 + (4.27167663181519 - 0.510410) * scal],  # D1 Rb87
    ["D1 - Rb87_F12", D1_center_87 + (4.27167663181519 + 0.306246) * scal],
    ["D1 - Rb87_F21", D1_center_87 + (-2.5630059790891 - 0.510410) * scal],
    ["D1 - Rb87_F22", D1_center_87 + (-2.5630059790891 + 0.306246) * scal],
    ["D2 - Rb85_F21", D2_center_85 + (1.770843922 - 0.113307) * scal],  # D2 Rb85
    ["D2 - Rb85_F22", D2_center_85 + (1.770843922 - 0.083955) * scal],
    ["D2 - Rb85_F23", D2_center_85 + (1.770843922 - 0.020503) * scal],
    ["D2 - Rb85_F32", D2_center_85 + (-1.264888516 - 0.083955) * scal],
    ["D2 - Rb85_F33", D2_center_85 + (-1.264888516 - 0.020503) * scal],
    ["D2 - Rb85_F34", D2_center_85 + (-1.264888516 + 0.100357) * scal],
    ["D2 - Rb87_F10", D2_center_87 + (4.27167663181519 - 0.3020738) * scal],  # D2 Rb87
    ["D2 - Rb87_F11", D2_center_87 + (4.27167663181519 - 0.2298518) * scal],
    ["D2 - Rb87_F12", D2_center_87 + (4.27167663181519 - 0.0729113) * scal],
    ["D2 - Rb87_F21", D2_center_87 + (-2.5630059790891 - 0.2298518) * scal],
    ["D2 - Rb87_F22", D2_center_87 + (-2.5630059790891 - 0.0729113) * scal],
    ["D2 - Rb87_F23", D2_center_87 + (-2.5630059790891 + 0.1937408) * scal],
    ["Rb85_D1_center", D1_center_85],
    ["Rb87_D1_center", D1_center_87],
    ["Rb85_D2_center", D2_center_85],
    ["Rb87_D2_center", D2_center_87],
    ["762_D3/2_center", two_photon_762],
    ["776_D5/2_center", two_photon_776_52],
    ["776_D3/2_center", two_photon_776_32],
    ["778_D5/2_center", two_photon_778],
]


class WavemeterReadoutError(RuntimeError):
    """Raised when a wavemeter measurement cannot be obtained or parsed."""


def compute_two_photon_detuning(f1: float, f2: float):
    """
    Helper function to compute the two photon detuning
    to a F to F'' manifold transition in Rubidium
    """
    f_sum = f1 + f2
    f_diff = f1 - f2
    scal = 1e-3

    two_photon_detuning = [
        ["absolute_diff (GHz)", f_diff],
        [
            "Rb85_D5/2_F2,0-4 (MHz)",
            f_sum - (D2_center_85 + two_photon_776_52) - 1.770843922 * scal,
        ],
        [
            "Rb85_D5/2_F3,1-5 (MHz)",
            f_sum - (D2_center_85 + two_photon_776_52) + 1.264888516 * scal,
        ],
        [
            "Rb87_D5/2_F1,1-3 (MHz)",
            f_sum - (D2_center_87 + two_photon_776_52) - 4.27167663181519 * scal,
        ],
        [
            "Rb87_D5/2_F2,1-4 (MHz)",
            f_sum - (D2_center_87 + two_photon_776_52) + 2.5630059790891 * scal,
        ],
    ]
    return two_photon_detuning


# ls_frequency = float(urllib.request.urlopen(f"http://{IP.WAVEMETER}:8000/api/{channel}/").read().decode("ascii")) - 377.107385690
def single_readout(
    channel: int, reference_f: float = 0, label: str = "", printing: bool = True
) -> float:
    """
    Fetch the laser frequency once from a wavemeter channel with urllib
    and compare to a reference frequency.

    Parameters:
        channel (int): Wavemeter channel (0-8 or 0-4 depending on switch)
        reference_f (float): Reference frequency value which is substracted. Default is 0.
        label (str): Optional labeling of the print
        printing (bool): Enable/Disable printing of the readout
    """
    timeout = 1  # Request timeout in secs

    try:
        ls_frequency = float(
            urllib.request.urlopen(f"http://{IP.WAVEMETER}:8000/api/{channel}/", timeout=timeout)
            .read()
            .decode("ascii")
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise WavemeterReadoutError(f"Failed to read wavemeter channel {channel}") from exc

    ls_frequency -= reference_f
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


def set_pid_setpoint(freq: float, channel: int):
    """
    Set the PID-setpoint of the wavemeter lock.
    The regulation has to be turned on manually due to safety reasons.

    Parameters:
        channel (int): Which channel the setpoint should be changed
        freq (float): Frequency in THz to be set.
    """

    urllib.request.urlopen(
        f"http://{IP.WAVEMETER}:8000/api/set_pid/",
        data=f"freq_thz={freq}&channel={channel}".encode("ascii"),
    )
    print(f"[WS-7] Set new PID-setpoint of channel {channel} to be {freq} THz.")


def track_frequency(total_time, time_step, save_path, channel, reference_f=0, save_csv=False):
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
                    urllib.request.urlopen(f"http://{IP.WAVEMETER}:8000/api/{channel}/", timeout=2)
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
    header = ["Transition"] + ["Frequencies (THz)"] + [f"Detuning (ch{c}) / GHz" for c in channels]
    rows = []
    freqs = [single_readout(c, reference_f=0, printing=False) for c in channels]

    rows.append(["absolute freq (THz)"] + [""] + [f for f in freqs])
    for label, f in Rb_transitions:
        row = [label] + [f] + [(freqs[c] - f) * 1e3 for c in channels]
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
