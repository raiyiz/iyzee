import pytest

from iyzee.experiment.procedures import (
    BandwidthStep,
    FrequencyStep,
    bandwidth_sweep_steps,
    run_bandwidth_sweep,
    run_frequency_sweep,
)
from iyzee.experiment.step import ExperimentContext


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


def test_bandwidth_step_sets_vbw_to_twice_rbw(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    mx = FakeMXA()
    ctx = ExperimentContext(mx=mx, run_id="t")

    result = BandwidthStep(rbw_hz=1000).run(ctx)

    assert mx.rbw_values == [1000]
    assert mx.vbw_values == [(2000, False)]
    assert result.x_value == 1000
    assert result.x_unit == "Hz"
    assert result.traces["squeezing"] == [1]
    assert result.traces["shot_noise"] == [2]
    assert result.meta == {"rbw_hz": 1000, "vbw_hz": 2000}


def test_bandwidth_sweep_steps_defaults_match_20khz_scan():
    steps = bandwidth_sweep_steps()

    assert [s.rbw_hz for s in steps] == [20e3 * i for i in range(1, 20)]


def test_frequency_step_opens_shutter_only_for_squeezing(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    setpoints = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.set_pid_setpoint",
        lambda freq, channel: setpoints.append((freq, channel)),
    )
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

    mx = FakeMXA()
    shutter = FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = FrequencyStep(frequency_thz=377.1, wavemeter_channel=1, relax_time_s=0.0).run(ctx)

    assert setpoints == [(377.1, 1)]
    assert shutter.events == ["open", "close"]
    assert mx.trace_calls == [1, 2]
    assert result.traces["squeezing"] == [1]
    assert result.traces["shot_noise"] == [2]


def test_frequency_step_requires_shutter():
    ctx = ExperimentContext(mx=FakeMXA(), run_id="t")

    with pytest.raises(ValueError, match="shutter"):
        FrequencyStep(frequency_thz=1.0, wavemeter_channel=1, relax_time_s=0.0).run(ctx)


def test_run_bandwidth_sweep_uses_caller_owned_mxa(monkeypatch):
    mx = FakeMXA()
    prepared = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.prepare_analyzer",
        lambda analyzer, traces, config: prepared.append((analyzer, traces, config)),
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.bandwidth_sweep_steps",
        lambda rbw_values_hz=None: [],
    )

    result = run_bandwidth_sweep(mx)

    assert result == []
    assert prepared and prepared[0][0] is mx


def test_run_bandwidth_sweep_does_not_disconnect_on_failure(monkeypatch):
    mx = FakeMXA()
    monkeypatch.setattr("iyzee.experiment.procedures.prepare_analyzer", lambda *args: None)
    monkeypatch.setattr(
        "iyzee.experiment.procedures.bandwidth_sweep_steps",
        lambda rbw_values_hz=None: [BoomStep()],
    )

    with pytest.raises(RuntimeError, match="boom"):
        run_bandwidth_sweep(mx)


def test_run_frequency_sweep_uses_caller_owned_devices(monkeypatch):
    mx = FakeMXA()
    shutter = FakeShutterControl()
    prepared = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.prepare_analyzer",
        lambda analyzer, traces, config: prepared.append((analyzer, traces, config)),
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.frequency_sweep_steps",
        lambda **kwargs: [],
    )

    result = run_frequency_sweep(mx, shutter)

    assert result == []
    assert prepared and prepared[0][0] is mx
