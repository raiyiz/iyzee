import pytest

from iyzee.experiment.core import ExperimentContext, StepResult
from iyzee.experiment.procedures import (
    AnalyzerConfig,
    BandwidthStep,
    FrequencyStep,
    SweepSetupError,
    acquire_trace,
    bandwidth_sweep_steps,
    build_bandwidth_sweep,
    build_frequency_sweep,
    run_bandwidth_sweep,
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
    steps, config = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)

    assert [step.rbw_hz for step in steps] == [20e3, 40e3]
    assert config.avg_count == 200
    assert config.sweep_duration_ms == 10
    assert config.res_bw_hz == 24e3
    assert config.trig_source == "IMM"


def test_frequency_sweep_builder_derives_relax_time_from_config():
    steps, config = build_frequency_sweep(
        laser_center_thz=377.1,
        wavemeter_channel=4,
        offsets_thz=[-1e-9, 0.0, 1e-9],
        sweep_duration_ms=10,
    )

    assert [step.frequency_thz for step in steps] == pytest.approx(
        [377.099999999, 377.1, 377.100000001]
    )
    assert [step.relax_time_s for step in steps] == [1.5, 1.5, 1.5]
    assert config.avg_count == 150
    assert config.sweep_duration_ms == 10


def test_frequency_step_opens_shutter_only_for_squeezing(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

    mx = FakeMXA()
    shutter = FakeShutterControl()
    setpoints = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.set_pid_setpoint",
        lambda freq, channel: setpoints.append((freq, channel)),
    )
    monkeypatch.setattr(
        "iyzee.experiment.procedures.read_frequency",
        lambda channel: 377.100001,
    )
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = FrequencyStep(frequency_thz=377.1, wavemeter_channel=1, relax_time_s=0.0).run(ctx)

    assert setpoints == [(377.1, 1)]
    assert shutter.events == ["open", "close"]
    assert mx.trace_calls == [1, 2]
    assert result.traces["squeezing"] == [1]
    assert result.traces["shot_noise"] == [2]
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100001)


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


def test_run_sweep_owns_setup_and_run_context_but_forwards_live_progress(monkeypatch):
    mx = FakeMXA()
    config = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)[1]
    steps = [BandwidthStep(20e3)]
    prepared = []
    seen = []

    monkeypatch.setattr(
        "iyzee.experiment.procedures.prepare_analyzer",
        lambda analyzer, traces, cfg: prepared.append((analyzer, traces, cfg)),
    )

    result = StepResult(
        label="rbw=20000Hz",
        x_value=20e3,
        x_unit="Hz",
        traces={"squeezing": [1], "shot_noise": [2]},
    )

    def fake_run_sequence(run_steps, ctx, *, on_error, on_step):
        seen.append((list(run_steps), ctx, on_error))
        on_step(0, 1, run_steps[0], result, None)
        return [result]

    monkeypatch.setattr("iyzee.experiment.procedures.run_sequence", fake_run_sequence)

    def on_step(record, index, total, step, step_result, error):
        seen.append((record, index, total, step, step_result, error))

    record, results = run_sweep(mx, steps, config, on_error="skip", on_step=on_step)

    assert results == [result]
    assert prepared == [(mx, (1, 2), config)]
    assert seen[0][0] == steps
    assert seen[0][1].mx is mx
    assert seen[0][1].run_id == record.run_id
    assert seen[0][1].config["res_bw_hz"] == config.res_bw_hz
    assert seen[0][2] == "skip"
    assert seen[1][0] is record
    assert seen[1][1:] == (0, 1, steps[0], result, None)


def test_run_sweep_distinguishes_analyzer_setup_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise TimeoutError("analyzer did not respond")

    monkeypatch.setattr("iyzee.experiment.procedures.prepare_analyzer", fail)

    with pytest.raises(SweepSetupError, match="analyzer did not respond"):
        run_sweep(FakeMXA(), [BandwidthStep(20e3)], AnalyzerConfig())


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
