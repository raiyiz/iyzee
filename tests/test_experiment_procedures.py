import itertools
from types import SimpleNamespace

import pytest

from iyzee.experiment.core import ExperimentContext
from iyzee.experiment.procedures import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    AcquisitionTimeoutError,
    AnalyzerConfig,
    BandwidthStep,
    FatalSweepError,
    FrequencyStep,
    Sweep,
    SweepStep,
    acquire_trace,
    acquisition_timeout_s,
    bandwidth_sweep_config,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
    estimate_sweep_duration_s,
    frequency_sweep_config,
    frequency_sweep_steps,
    run_bandwidth_sweep,
    run_frequency_sweep,
    run_sweep,
)


class FakeMXA:
    def __init__(self):
        self.rbw_values = []
        self.vbw_values = []
        self.trace_calls = []

    def set_rbw(self, rbw_hz):
        self.rbw_values.append(rbw_hz)

    def set_vbw(self, vbw_hz, auto=False):
        self.vbw_values.append((vbw_hz, auto))


def fake_acquire_trace(mx, trace_num):
    mx.trace_calls.append(trace_num)
    return [trace_num]


class BoomStep:
    label = "boom"

    def run(self, ctx):
        raise RuntimeError("boom")


class FakeShutterControl:
    """Stand-in for power.ShutterControl as a context manager."""

    def __init__(self):
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def open(self):
        self.events.append("open")

    def close(self):
        self.events.append("close")


def _quiet(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)


def _wavemeter(events, frequency=377.1):
    return SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: events.append(("set", freq, channel)),
        read_frequency=lambda channel: events.append(("read", channel)) or frequency,
    )


class _AcquisitionMXA:
    def __init__(self, *, completed=True, values=(1.0, 2.0, 3.0)):
        self.completed = completed
        self.values = list(values)
        self.trace_updates = []
        self.timeouts = []
        self.reads = []
        self.aborts = 0

    def set_trace_update(self, trace_num, state):
        self.trace_updates.append((trace_num, state))

    def single_sweep_wait(self, timeout_sec=30.0):
        self.timeouts.append(timeout_sec)
        return self.completed

    def get_trace_data(self, trace_num=1, binary=True):
        self.reads.append((trace_num, binary))
        return self.values

    def abort_sweep(self):
        self.aborts += 1


def test_trace_acquisition_rejects_timeout_and_invalid_data():
    stalled = _AcquisitionMXA(completed=False)
    with pytest.raises(AcquisitionTimeoutError, match=r"trace 1 did not finish within 12\.5 s"):
        acquire_trace(stalled, 1, timeout_sec=12.5)
    assert stalled.trace_updates == [(1, True), (1, False)]
    assert stalled.timeouts == [12.5]
    assert stalled.reads == []
    assert stalled.aborts == 1

    acquired = _AcquisitionMXA()
    assert acquire_trace(acquired, 2, timeout_sec=20.0) == [1.0, 2.0, 3.0]
    assert acquired.reads == [(2, True)]  # use binary transfer; no long ASCII response
    assert acquired.trace_updates == [(2, True), (2, False)]
    assert acquired.aborts == 0

    invalid = _AcquisitionMXA(values=(1.0, float("nan")))
    with pytest.raises(FatalSweepError, match="non-finite"):
        acquire_trace(invalid, 1, timeout_sec=20.0)
    assert invalid.trace_updates[-1] == (1, False)
    assert invalid.aborts == 1


def test_frequency_timeout_and_duration_estimates_follow_averaging_work():
    config = frequency_sweep_config(sweep_duration_ms=500)
    assert acquisition_timeout_s(config) == pytest.approx(155.0)

    sweep = build_frequency_sweep(offsets_thz=[0.0, 1e-6], sweep_duration_ms=500)
    # Two traces per point, 150 averages per trace, plus 0.5 s laser settling per point.
    assert estimate_sweep_duration_s(sweep) == pytest.approx(301.0)


# -- builders and defaults ----------------------------------------------------------------------


def test_sweep_builders_and_their_steps_declare_what_they_need():
    # -- the builders and their defaults
    steps = bandwidth_sweep_steps()
    assert [s.rbw_hz for s in steps] == [20e3 * i for i in range(1, 20)]

    sweep = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)
    assert sweep.kind == "bandwidth" and [step.rbw_hz for step in sweep.steps] == [20e3, 40e3]
    config = sweep.config
    assert (config.avg_count, config.sweep_duration_ms) == (200, 10)
    assert (config.res_bw_hz, config.trig_source) == (24e3, "IMM")

    # The frequency scan settles for the laser, not for the analyzer's workload.
    sweep = build_frequency_sweep(
        laser_center_thz=377.1,
        wavemeter_channel=4,
        offsets_thz=[-1e-9, 0.0, 1e-9],
        sweep_duration_ms=10,
    )
    assert sweep.kind == "frequency"
    assert [step.frequency_thz for step in sweep.steps] == pytest.approx(
        [377.099999999, 377.1, 377.100000001]
    )
    assert [step.relax_time_s for step in sweep.steps] == [0.5, 0.5, 0.5]
    assert sweep.config.avg_count == 150
    assert build_frequency_sweep(offsets_thz=[0.0], relax_time_s=2.0).steps[0].relax_time_s == 2.0

    assert DEFAULT_SWEEP_DURATION_MS == 500  # everywhere, and overridable
    for config in (AnalyzerConfig(), bandwidth_sweep_config(), frequency_sweep_config()):
        assert config.sweep_duration_ms == 500
    assert build_bandwidth_sweep(sweep_duration_ms=25).config.sweep_duration_ms == 25
    assert build_frequency_sweep(sweep_duration_ms=25).config.sweep_duration_ms == 25
    for config_for in (bandwidth_sweep_config, frequency_sweep_config):
        config = config_for(sweep_duration_ms=7, res_bw_hz=1e3)
        assert (config.sweep_duration_ms, config.res_bw_hz) == (7, 1e3)

    assert pytest.approx(10) == FREQUENCY_STEP_THZ * 1e6  # 10 MHz steps (THz -> MHz)
    freq_steps = frequency_sweep_steps(laser_center_thz=377.0)
    gaps = [b.frequency_thz - a.frequency_thz for a, b in itertools.pairwise(freq_steps)]
    assert gaps and all(gap == pytest.approx(10e-6) for gap in gaps)
    assert 377.0 in [pytest.approx(s.frequency_thz) for s in freq_steps]  # still from the centre

    # -- every sweep kind is a step that names itself and declares what it needs
    assert set(SWEEP_KINDS) == {"bandwidth", "frequency"}
    for kind, step_type in SWEEP_KINDS.items():
        assert issubclass(step_type, SweepStep) and step_type.kind == kind
        assert "mxa" in step_type.requires
    assert BandwidthStep.requires == ("mxa",)
    assert FrequencyStep.requires == ("mxa", "shutter", "wavemeter")
    for sweep in (build_bandwidth_sweep(), build_frequency_sweep()):
        assert isinstance(sweep, Sweep) and sweep.requires == SWEEP_KINDS[sweep.kind].requires
        assert sweep.steps and all(isinstance(s, SWEEP_KINDS[sweep.kind]) for s in sweep.steps)


# -- steps --------------------------------------------------------------------------------------


def test_each_step_drives_the_caller_owned_devices_in_order(monkeypatch):
    # -- a bandwidth step sets vbw to twice rbw and ignores the shutter
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    mx, shutter = FakeMXA(), FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = BandwidthStep(rbw_hz=1000).run(ctx)

    assert mx.rbw_values == [1000] and mx.vbw_values == [(2000, False)]
    assert (result.x_value, result.x_unit) == (1000, "Hz")
    assert result.traces == {"squeezing": [1], "shot_noise": [2]}
    assert result.meta == {"rbw_hz": 1000, "vbw_hz": 2000}
    assert shutter.events == []

    # -- a frequency step goes there settles reads then records with the shutter
    events = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.time.sleep", lambda s: events.append(("sleep", s))
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.acquire_trace",
        lambda mx, trace_num: events.append(("acquire", trace_num)) or [trace_num],
    )
    shutter = FakeShutterControl()
    ctx = ExperimentContext(
        mx=FakeMXA(), run_id="t", shutter=shutter, wavemeter=_wavemeter(events, 377.100002)
    )

    result = FrequencyStep(frequency_thz=377.1, wavemeter_channel=4).run(ctx)

    assert events == [
        ("set", 377.1, 4),
        ("sleep", 0.5),
        ("read", 4),
        ("acquire", 1),
        ("acquire", 2),
    ]
    assert shutter.events == ["open", "close"]  # opened only for the squeezing trace
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100002)

    # -- sweeps run on the caller owned devices and never build their own
    _quiet(monkeypatch)
    prepared = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.prepare_analyzer",
        lambda analyzer, traces, config: prepared.append((analyzer, config)),
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.Wavemeter",
        lambda *a, **k: pytest.fail("a client was built although one was passed"),
        raising=False,
    )
    mx, shutter, events = FakeMXA(), FakeShutterControl(), []

    bandwidth = run_sweep(mx, build_bandwidth_sweep([1e3, 2e3]))
    frequency = run_frequency_sweep(
        mx, shutter, offsets_thz=[0.0], relax_time_s=0.0, wavemeter=_wavemeter(events)
    )

    assert [r.x_unit for r in bandwidth] == ["Hz", "Hz"] and [r.x_unit for r in frequency] == [
        "THz"
    ]
    assert [config.center_hz for _, config in prepared][:2] == [1e6, 1.5e6]
    assert [e[0] for e in events] == ["set", "read"] and shutter.events == ["open", "close"]
    assert frequency[0].meta["measured_frequency_thz"] == 377.1
    assert prepared[0][0] is mx  # the caller's analyzer

    monkeypatch.setattr(
        "iyzee.experiment.procedures.bandwidth_sweep_steps", lambda rbw_values_hz=None: [BoomStep()]
    )
    with pytest.raises(RuntimeError, match="boom"):  # a failure propagates; nothing is disconnected
        run_bandwidth_sweep(mx)


# -- running a sweep ----------------------------------------------------------------------------
