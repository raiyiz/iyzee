import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from iyzee import IP

from .mxa import KeysightMXA
from .power import ShutterControl
from .wavemeter_readout import set_pid_setpoint

TRACE_SQZ = 1
TRACE_SHOT = 2


DEFAULT_ANALYZER_PARAMS = {
    "center_hz": 1e6,
    "span_hz": 0,
    "avg_count": 100,
    "sweep_duration": 10,
    "res_bw": 10e3,
    "avg_type": "LOG",
    "trig_source": "EXT",
}


def prepare_analyzer(traces, **overrides):
    """Create and configure an MXA for a measurement procedure."""
    params = {**DEFAULT_ANALYZER_PARAMS, **overrides}
    mx = KeysightMXA(ip=IP.NOISE_ANALYZER)

    mx.set_center_freq(params["center_hz"])
    mx.set_span(params["span_hz"])
    mx.set_rbw(params["res_bw"])
    mx.set_vbw(params["res_bw"], auto=True)
    mx.set_attenuation_auto(True)
    mx.set_trigger_source(params["trig_source"])
    mx.set_sweep_duration(params["sweep_duration"])
    mx.set_average_count(params["avg_count"])
    mx.set_average_type(params["avg_type"])

    for trace in traces:
        mx.set_trace_display(trace, True)
        mx.set_trace_mode(trace, "AVER")

    return mx


def acquire_trace(mx, trace_num):
    """Acquire one trace and return its data."""
    mx.set_trace_update(trace_num, True)
    mx.single_sweep_wait()
    mx.set_trace_update(trace_num, False)
    return mx.get_trace_data(trace_num=trace_num, binary=False)


def record_bw_seq():
    """Measure squeezing/shot-noise traces while scanning RBW."""
    params = {
        "center_hz": 1.5e6,
        "span_hz": 1e5,
        "avg_count": 300,
        "sweep_duration": 10,
        "res_bw": 24e3,
    }
    mx = prepare_analyzer((TRACE_SQZ, TRACE_SHOT), **params)
    data = []

    try:
        # Scan in 20 kHz steps.
        for rbw_hz in (2 * i * 1e4 for i in range(1, 20)):
            mx.set_rbw(rbw_hz)
            # Keep the experimental VBW relationship explicit: VBW = 2 * RBW.
            mx.set_vbw(rbw_hz * 2, auto=False)
            squeezing = acquire_trace(mx, TRACE_SQZ)
            shot_noise = acquire_trace(mx, TRACE_SHOT)
            data.append((rbw_hz, squeezing, shot_noise))
    finally:
        mx.disconnect()

    return data


def record_freq_seq():
    """Measure squeezing/shot-noise traces while scanning laser frequency."""
    params = {
        "center_hz": 1.5e6,
        "span_hz": 0,
        "avg_count": 150,
        "sweep_duration": 10,
        "res_bw": 24e3,
    }
    laser_center_thz = 377.1052067
    wavemeter_channel = 1
    relax_time = params["sweep_duration"] * params["avg_count"] / 1000

    mx = prepare_analyzer((TRACE_SQZ, TRACE_SHOT), **params)
    data = []

    try:
        with ShutterControl() as shutter:
            for frequency_thz in (laser_center_thz + i * 10e-6 for i in range(-1, 1)):
                set_pid_setpoint(frequency_thz, wavemeter_channel)
                time.sleep(relax_time)

                try:
                    shutter.open()
                    squeezing = acquire_trace(mx, TRACE_SQZ)
                finally:
                    shutter.close()

                shot_noise = acquire_trace(mx, TRACE_SHOT)
                data.append((frequency_thz, squeezing, shot_noise))
    finally:
        mx.disconnect()

    return data


def multiplot(data):
    """Plot the squeezing-minus-shot-noise difference for each scan point."""
    fig, ax = plt.subplots()
    labels = []

    for x_value, squeezing, shot_noise in data:
        difference = np.asarray(squeezing) - np.asarray(shot_noise)
        ax.plot(difference)
        labels.append(f"value = {x_value}")

    if labels:
        ax.legend(labels, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.1))
    ax.set_xlabel("Trace point")
    ax.set_ylabel("Squeezing - shot noise")
    fig.tight_layout()
    plt.show()


def create_dirs(name: str = "") -> Path:
    """Create and return today's measurement-data directory."""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    data_dir = Path(__file__).parent / "data" / today / name
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def save_data(data, savedir: Path) -> Path:
    """Save variable-length trace data in a compressed NumPy archive."""
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = savedir / f"{timestamp}.npz"
    np.savez_compressed(path, data=np.asarray(data, dtype=object))
    return path


def main():
    """Run the currently selected measurement procedure."""
    data = record_bw_seq()
    print(f"{len(data)=}")
    multiplot(data)
    savedir = create_dirs()
    save_data(data, savedir)
    return data


if __name__ == "__main__":
    main()
