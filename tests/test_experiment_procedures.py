import pytest

from iyzee.devices.wavemeter import DEFAULT_CHANNEL, Wavemeter
from iyzee.experiment.core import ExperimentContext
from iyzee.experiment.procedures import (
    BandwidthStep,
    FrequencyStep,
    acquire_trace,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
    run_bandwidth_sweep,
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


def test_bandwidth_sweep_builder_uses_shared_config():
    steps, config = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)

    assert [step.rbw_hz for step in steps] == [20e3, 40e3]
    assert config.avg_count == 200
    assert config.sweep_duration_ms == 10
    assert config.res_bw_hz == 24e3
    assert config.trig_source == "IMM"


def test_frequency_sweep_builder_uses_the_laser_settle_time_not_the_analyzer_workload():
    steps, config = build_frequency_sweep(
        laser_center_thz=377.1,
        wavemeter_channel=4,
        offsets_thz=[-1e-9, 0.0, 1e-9],
        sweep_duration_ms=10,
    )

    assert [step.frequency_thz for step in steps] == pytest.approx(
        [377.099999999, 377.1, 377.100000001]
    )
    assert [step.relax_time_s for step in steps] == [0.5, 0.5, 0.5]
    assert config.avg_count == 150
    assert config.sweep_duration_ms == 10

    steps, _ = build_frequency_sweep(offsets_thz=[0.0], relax_time_s=2.0)
    assert [step.relax_time_s for step in steps] == [2.0]


def test_frequency_step_opens_shutter_only_for_squeezing(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

    mx = FakeMXA()
    shutter = FakeShutterControl()
    events = []
    monkeypatch.setattr(
        Wavemeter,
        "set_pid_setpoint",
        lambda self, freq, channel=DEFAULT_CHANNEL: events.append(("set", freq, channel)),
    )
    monkeypatch.setattr(
        Wavemeter,
        "read_frequency",
        lambda self, channel=DEFAULT_CHANNEL: events.append(("read", channel)) or 377.100001,
    )
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = FrequencyStep(frequency_thz=377.1, wavemeter_channel=1, relax_time_s=0.0).run(ctx)

    assert events == [("set", 377.1, 1), ("read", 1)]
    assert shutter.events == ["open", "close"]
    assert mx.trace_calls == [1, 2]
    assert result.traces["squeezing"] == [1]
    assert result.traces["shot_noise"] == [2]
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100001)


def test_frequency_step_sets_settles_half_a_second_reads_then_records(monkeypatch):
    """Per step: go to the frequency, settle, read it, only then take the traces."""
    events = []
    monkeypatch.setattr(
        Wavemeter,
        "set_pid_setpoint",
        lambda self, freq, channel=DEFAULT_CHANNEL: events.append(("set", freq, channel)),
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.time.sleep", lambda s: events.append(("sleep", s))
    )
    monkeypatch.setattr(
        Wavemeter,
        "read_frequency",
        lambda self, channel=DEFAULT_CHANNEL: events.append(("read", channel)) or 377.100002,
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.acquire_trace",
        lambda mx, trace_num: events.append(("acquire", trace_num)) or [trace_num],
    )
    ctx = ExperimentContext(mx=FakeMXA(), run_id="t", shutter=FakeShutterControl())

    result = FrequencyStep(frequency_thz=377.1, wavemeter_channel=4).run(ctx)

    assert events == [
        ("set", 377.1, 4),
        ("sleep", 0.5),
        ("read", 4),
        ("acquire", 1),
        ("acquire", 2),
    ]
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100002)


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


class FakeMXAForTraceAcquisition:
    def __init__(self):
        self.update_states = []

    def set_trace_update(self, trace_num, state):
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self):
        raise RuntimeError("sweep failed")


def test_acquire_trace_disables_trace_after_failure():
    mx = FakeMXAForTraceAcquisition()

    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_trace(mx, 1)

    assert mx.update_states == [(1, True), (1, False)]
