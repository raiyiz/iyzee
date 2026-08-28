import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from base import IP
from mxa import KeysightMXA
from power import ShutterControl
from wavemeter_readout import set_pid_setpoint


def prepare_analyzer(
    traces,
    center_hz=1e6,
    span_hz=0,
    avg_count=100,
    sweep_duration=10,
    res_bw=10e3,
    avg_type="LOG",
    trig_source="EXT",
    **kwargs,
):
    mx = KeysightMXA(ip=IP.NOISE_ANALYZER)
    mx.set_center_freq(center_hz)
    mx.set_span(span_hz)

    mx.set_rbw(res_bw)
    mx.set_vbw(res_bw, auto=True)

    mx.set_attenuation_auto(True)
    mx.set_trigger_source(trig_source)

    mx.set_sweep_duration(sweep_duration)
    mx.set_average_count(avg_count)
    mx.set_average_type(avg_type=avg_type)

    for tr in traces:
        mx.set_trace_display(tr, True)
        mx.set_trace_mode(tr, "AVER")
    return mx


def record_bw_seq():
    params = dict(
        center_hz=1.5e6,
        span_hz=1e5,
        avg_count=300,
        sweep_duration=10,
        res_bw=24e3,
    )
    traces = trace_sqz, trace_shot = 1, 2

    mx = prepare_analyzer(traces, **params)

    diffs = [
        2 * i * 10e4
        for i in range(
            1,
            20,
        )
    ]  # scan in 200kHz steps
    data = []

    for Δf in diffs:
        mx.set_rbw(Δf)

        mx.set_trace_update(trace_sqz, True)
        mx.single_sweep_wait()
        mx.set_trace_update(trace_sqz, False)

        mx.set_vbw(params["res_bw"] * 2, auto=False)  # experimental
        mx.set_trace_update(trace_shot, True)
        mx.single_sweep_wait()
        mx.set_trace_update(trace_shot, False)

        sqz = mx.get_trace_data(trace_num=trace_sqz, binary=False)
        sql = mx.get_trace_data(trace_num=trace_shot, binary=False)
        data.append([Δf, sqz, sql])

    return data


def record_freq_seq():
    sc = ShutterControl()
    params = dict(
        center_hz=1.5e6,
        span_hz=0,
        avg_count=150,
        sweep_duration=10,
        res_bw=24e3,
    )
    traces = trace_sqz, trace_shot = 1, 2

    laser_center = 377.1052067  # THz
    wavemeter_chan = 1
    relax_time = params["sweep_duration"] * params["avg_count"] / 1000  # from ms to sec

    mx = prepare_analyzer(traces, **params)

    diffs = [i * 10e-6 for i in range(-1, 1)]  # scan in 10MHz steps
    data = []

    for Δf in diffs:
        f = laser_center + Δf
        set_pid_setpoint(f, wavemeter_chan)
        time.sleep(relax_time)  # allow the laser to stabilize

        sc.open()
        mx.set_trace_update(trace_sqz, True)
        mx.single_sweep_wait()
        mx.set_trace_update(trace_sqz, False)
        sc.close()
        mx.set_trace_update(trace_shot, True)
        mx.single_sweep_wait()
        mx.set_trace_update(trace_shot, False)

        sqz = mx.get_trace_data(trace_num=trace_sqz, binary=False)
        sql = mx.get_trace_data(trace_num=trace_shot, binary=False)
        data.append([f, sqz, sql])

    return data


def multiplot(data):
    # Have a look at the colormaps here and decide which one you'd like:
    # http://matplotlib.org/1.2.1/examples/pylab_examples/show_colormaps.html

    num_plots = len(data)
    colormap = plt.cm.gist_ncar
    plt.gca().set_prop_cycle(
        plt.cycler("color", plt.cm.viridis(np.linspace(0, 1, num_plots)))
    )
    labels = []
    for f, sqz, shot in data:
        diff = np.array(sqz) - np.array(shot)
        plt.plot(diff)
        labels.append(f"freq = {f} THz")
        # labels.append(r'$y = %ix + %i$' % (i, 5*i))
        # I'm basically just demonstrating several different legend options here...
        plt.legend(
            labels,
            ncol=4,
            loc="upper center",
            bbox_to_anchor=[0.5, 1.1],
            columnspacing=1.0,
            labelspacing=0.0,
            handletextpad=0.0,
            handlelength=1.5,
            fancybox=True,
            shadow=True,
        )

        plt.show()


def main():
    data = record_bw_seq()

    print(f"{len(data)=}")
    multiplot(data)
    savedir = create_dirs()

    now = datetime.now().isoformat()
    np.savetxt(savedir / now + ".gz")

    return data


def create_dirs(name: str = ""):
    t = datetime.today()
    todaystr = f"{t.year}-{t.month:0>2}-{t.day:0>2}"

    data_dir = Path(__file__).parent / "data"
    curdir = data_dir / todaystr / name
    curdir.mkdir(exist_ok=True)
    return curdir


if __name__ == "__main__":
    data = main()
