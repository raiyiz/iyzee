from types import SimpleNamespace

import pytest

from iyzee.devices.wavemeter import Wavemeter
from iyzee.experiment.core import ExperimentContext
from iyzee.experiment.procedures import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    AnalyzerConfig,
    BandwidthStep,
    FrequencyStep,
    Sweep,
    SweepStep,
    acquire_pair,
    acquire_trace,
    bandwidth_sweep_config,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
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
    sweep = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)
    config = sweep.config

    assert sweep.kind == "bandwidth"
    assert [step.rbw_hz for step in sweep.steps] == [20e3, 40e3]
    assert config.avg_count == 200
    assert config.sweep_duration_ms == 10
    assert config.res_bw_hz == 24e3
    assert config.trig_source == "IMM"


def test_frequency_sweep_builder_uses_the_laser_settle_time_not_the_analyzer_workload():
    sweep = build_frequency_sweep(
        laser_center_thz=377.1,
        wavemeter_channel=4,
        offsets_thz=[-1e-9, 0.0, 1e-9],
        sweep_duration_ms=10,
    )
    steps, config = sweep.steps, sweep.config

    assert sweep.kind == "frequency"
    assert [step.frequency_thz for step in steps] == pytest.approx(
        [377.099999999, 377.1, 377.100000001]
    )
    assert [step.relax_time_s for step in steps] == [0.5, 0.5, 0.5]
    assert config.avg_count == 150
    assert config.sweep_duration_ms == 10

    sweep = build_frequency_sweep(offsets_thz=[0.0], relax_time_s=2.0)
    assert [step.relax_time_s for step in sweep.steps] == [2.0]


def test_frequency_step_opens_shutter_only_for_squeezing(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

    mx = FakeMXA()
    shutter = FakeShutterControl()
    events = []
    wavemeter = SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: events.append(("set", freq, channel)),
        read_frequency=lambda channel: events.append(("read", channel)) or 377.100001,
    )
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter, wavemeter=wavemeter)

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
    wavemeter = SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: events.append(("set", freq, channel)),
        read_frequency=lambda channel: events.append(("read", channel)) or 377.100002,
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.time.sleep", lambda s: events.append(("sleep", s))
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.acquire_trace",
        lambda mx, trace_num: events.append(("acquire", trace_num)) or [trace_num],
    )
    ctx = ExperimentContext(
        mx=FakeMXA(), run_id="t", shutter=FakeShutterControl(), wavemeter=wavemeter
    )

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


def test_frequency_step_requires_the_wavemeter_and_touches_nothing_without_it():
    mx = FakeMXA()
    shutter = FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    with pytest.raises(ValueError, match=r"ctx\.wavemeter"):
        FrequencyStep(frequency_thz=1.0, wavemeter_channel=1, relax_time_s=0.0).run(ctx)

    assert shutter.events == [] and mx.trace_calls == []


def test_frequency_step_names_everything_the_context_lacks():
    ctx = ExperimentContext(mx=FakeMXA(), run_id="t")

    with pytest.raises(ValueError, match=r"ctx\.shutter, ctx\.wavemeter"):
        FrequencyStep(frequency_thz=1.0, wavemeter_channel=1).run(ctx)


def test_frequency_step_never_builds_a_wavemeter_of_its_own(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "iyzee.experiment.procedures.Wavemeter",
        lambda *a, **k: pytest.fail("a step must use ctx.wavemeter"),
        raising=False,
    )
    wavemeter = SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: None, read_frequency=lambda channel: 377.1
    )
    ctx = ExperimentContext(
        mx=FakeMXA(), run_id="t", shutter=FakeShutterControl(), wavemeter=wavemeter
    )

    FrequencyStep(frequency_thz=377.1, wavemeter_channel=1, relax_time_s=0.0).run(ctx)


def test_run_frequency_sweep_uses_the_wavemeter_it_is_given(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)
    monkeypatch.setattr("iyzee.experiment.procedures.prepare_analyzer", lambda *a: None)
    monkeypatch.setattr(
        "iyzee.experiment.procedures.Wavemeter",
        lambda *a, **k: pytest.fail("a client was built although one was passed"),
        raising=False,
    )
    calls = []
    wavemeter = SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: calls.append(("set", freq, channel)),
        read_frequency=lambda channel: calls.append(("read", channel)) or 377.1,
    )

    results = run_frequency_sweep(
        FakeMXA(),
        FakeShutterControl(),
        offsets_thz=[0.0],
        relax_time_s=0.0,
        wavemeter=wavemeter,
    )

    assert [c[0] for c in calls] == ["set", "read"]
    assert results[0].meta["measured_frequency_thz"] == 377.1


def test_the_sweep_duration_defaults_to_10_ms_everywhere_and_is_overridable():
    assert DEFAULT_SWEEP_DURATION_MS == 10
    for config in (AnalyzerConfig(), bandwidth_sweep_config(), frequency_sweep_config()):
        assert config.sweep_duration_ms == 10
    assert build_bandwidth_sweep().config.sweep_duration_ms == 10
    assert build_frequency_sweep().config.sweep_duration_ms == 10
    assert build_bandwidth_sweep(sweep_duration_ms=25).config.sweep_duration_ms == 25
    assert build_frequency_sweep(sweep_duration_ms=25).config.sweep_duration_ms == 25


def test_a_frequency_scan_defaults_to_10_mhz_steps():
    assert FREQUENCY_STEP_THZ * 1e6 == pytest.approx(10)  # THz -> MHz
    steps = frequency_sweep_steps(laser_center_thz=377.0)
    gaps = [b.frequency_thz - a.frequency_thz for a, b in zip(steps, steps[1:], strict=False)]
    assert gaps and all(gap == pytest.approx(10e-6) for gap in gaps)
    assert 377.0 in [pytest.approx(s.frequency_thz) for s in steps]  # still scans from the centre


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


# -- the two sweeps share one structure ----------------------------------------------------


def test_every_sweep_kind_is_a_sweep_step_that_names_itself():
    assert set(SWEEP_KINDS) == {"bandwidth", "frequency"}
    for kind, step_type in SWEEP_KINDS.items():
        assert issubclass(step_type, SweepStep)
        assert step_type.kind == kind
        assert "mxa" in step_type.requires


def test_the_instruments_a_sweep_requires_are_declared_on_its_step():
    assert BandwidthStep.requires == ("mxa",)
    assert FrequencyStep.requires == ("mxa", "shutter", "wavemeter")
    assert build_bandwidth_sweep().requires == ("mxa",)
    assert build_frequency_sweep().requires == ("mxa", "shutter", "wavemeter")


def test_both_builders_return_a_sweep_with_matching_steps_and_config():
    for sweep in (build_bandwidth_sweep(), build_frequency_sweep()):
        assert isinstance(sweep, Sweep)
        assert sweep.steps and all(isinstance(s, SWEEP_KINDS[sweep.kind]) for s in sweep.steps)


def test_both_analyzer_presets_take_the_same_options():
    for config_for in (bandwidth_sweep_config, frequency_sweep_config):
        config = config_for(sweep_duration_ms=7, res_bw_hz=1e3)
        assert (config.sweep_duration_ms, config.res_bw_hz) == (7, 1e3)


def test_acquire_pair_without_a_shutter_takes_both_traces(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    mx = FakeMXA()

    assert acquire_pair(mx) == {"squeezing": [1], "shot_noise": [2]}
    assert mx.trace_calls == [1, 2]


def test_acquire_pair_closes_the_shutter_even_if_the_squeezing_trace_fails(monkeypatch):
    def failing(mx, trace_num):
        raise RuntimeError("sweep failed")

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", failing)
    shutter = FakeShutterControl()

    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_pair(FakeMXA(), shutter)

    assert shutter.events == ["open", "close"]


def test_a_bandwidth_step_ignores_a_shutter_in_the_context(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    shutter = FakeShutterControl()
    ctx = ExperimentContext(mx=FakeMXA(), run_id="t", shutter=shutter)

    BandwidthStep(rbw_hz=1000).run(ctx)

    assert shutter.events == []


def test_run_sweep_runs_any_sweep_with_the_caller_owned_devices(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)
    monkeypatch.setattr(Wavemeter, "set_pid_setpoint", lambda self, freq, channel=1: None)
    monkeypatch.setattr(Wavemeter, "read_frequency", lambda self, channel=1: 377.1)
    prepared = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.prepare_analyzer",
        lambda analyzer, traces, config: prepared.append((analyzer, config)),
    )
    mx, shutter = FakeMXA(), FakeShutterControl()

    bandwidth = run_sweep(mx, build_bandwidth_sweep([1e3, 2e3]))
    frequency = run_frequency_sweep(
        mx, shutter, laser_center_thz=377.1, offsets_thz=[0.0], relax_time_s=0.0
    )

    assert [r.x_unit for r in bandwidth] == ["Hz", "Hz"]
    assert [r.x_unit for r in frequency] == ["THz"]
    assert shutter.events == ["open", "close"]
    assert [config.center_hz for _, config in prepared] == [1e6, 1.5e6]
