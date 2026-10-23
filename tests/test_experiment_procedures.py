import itertools
from types import SimpleNamespace

import pytest

from iyzee.experiment.core import ExperimentContext
from iyzee.experiment.procedures import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SHUTTER_SETTLE_TIME_S,
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
    read_analyzer_settings,
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

    def __init__(self, *, can_open=True):
        self.events = []
        self._open = False
        self.can_open = can_open

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def open(self):
        self.events.append("open")
        self._open = True

    def close(self):
        self.events.append("close")
        self._open = False

    def is_open(self):
        return self._open and self.can_open


class FakeMXAForTraceAcquisition:
    def __init__(self):
        self.update_states = []

    def set_trace_update(self, trace_num, state):
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self):
        raise RuntimeError("sweep failed")


class FakeMXATimeout:
    def __init__(self):
        self.update_states = []

    def set_trace_update(self, trace_num, state):
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self):
        return False

    def get_trace_data(self, **kwargs):
        pytest.fail("trace data must not be read after a sweep timeout")


def _quiet(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)


def _wavemeter(events, frequency=377.1):
    return SimpleNamespace(
        set_pid_setpoint=lambda freq, channel: events.append(("set", freq, channel)),
        read_frequency=lambda channel: events.append(("read", channel)) or frequency,
    )


# -- builders and defaults ----------------------------------------------------------------------


def test_the_builders_and_their_defaults():
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

    assert DEFAULT_SWEEP_DURATION_MS == 10  # everywhere, and overridable
    for config in (AnalyzerConfig(), bandwidth_sweep_config(), frequency_sweep_config()):
        assert config.sweep_duration_ms == 10
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


def test_every_sweep_kind_is_a_step_that_names_itself_and_declares_what_it_needs():
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


def test_a_bandwidth_step_sets_vbw_to_twice_rbw_and_ignores_the_shutter(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    mx, shutter = FakeMXA(), FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = BandwidthStep(rbw_hz=1000).run(ctx)

    assert mx.rbw_values == [1000] and mx.vbw_values == [(2000, False)]
    assert (result.x_value, result.x_unit) == (1000, "Hz")
    assert result.traces == {"squeezing": [1], "shot_noise": [2]}
    assert result.meta == {
        "requested_rbw_hz": 1000,
        "requested_vbw_hz": 2000,
        "rbw_hz": 1000,
        "vbw_hz": 2000,
    }
    assert shutter.events == []


def test_bandwidth_step_uses_applied_instrument_value_as_the_sweep_coordinate(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)

    class QuantizingMXA(FakeMXA):
        def query(self, command):
            return {"BWID?": "976.0", "BWID:VID?": "1952.0"}[command]

    result = BandwidthStep(1000).run(ExperimentContext(mx=QuantizingMXA(), run_id="quantized"))
    assert result.x_value == 976.0
    assert result.label == "rbw=976Hz"
    assert result.meta == {
        "requested_rbw_hz": 1000,
        "requested_vbw_hz": 2000,
        "rbw_hz": 976.0,
        "vbw_hz": 1952.0,
    }


def test_read_analyzer_settings_records_values_reported_by_the_mxa():
    class MXA:
        def query(self, command):
            return {
                "BWID?": "976.0",
                "SWE:TIME?": "0.01",
                "AVER:COUN?": "200",
            }[command]

    assert read_analyzer_settings(MXA()) == {
        "rbw_hz": 976.0,
        "sweep_duration_s": 0.01,
        "average_count": 200,
    }


def test_a_frequency_step_goes_there_settles_reads_then_records_with_the_shutter(monkeypatch):
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
        ("sleep", SHUTTER_SETTLE_TIME_S),
        ("acquire", 1),
        ("sleep", SHUTTER_SETTLE_TIME_S),
        ("acquire", 2),
    ]
    assert shutter.events == ["open", "close"]  # opened only for the squeezing trace
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100002)


def test_a_frequency_step_aborts_if_the_laser_never_reaches_its_setpoint(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda _: None)
    monkeypatch.setattr("iyzee.experiment.procedures.FREQUENCY_LOCK_TIMEOUT_S", 0.0)
    events = []
    ctx = ExperimentContext(
        mx=FakeMXA(),
        run_id="t",
        shutter=FakeShutterControl(),
        wavemeter=_wavemeter(events, 377.101),
    )
    with pytest.raises(TimeoutError, match="laser did not reach"):
        FrequencyStep(frequency_thz=377.1, wavemeter_channel=4).apply(ctx)
    assert events == [("set", 377.1, 4), ("read", 4)]


def test_a_frequency_step_names_what_the_context_lacks_and_touches_nothing_without_it():
    with pytest.raises(ValueError, match=r"ctx\.shutter, ctx\.wavemeter"):
        FrequencyStep(frequency_thz=1.0, wavemeter_channel=1).run(
            ExperimentContext(mx=FakeMXA(), run_id="t")
        )

    mx, shutter = FakeMXA(), FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)
    with pytest.raises(ValueError, match=r"ctx\.wavemeter"):
        FrequencyStep(frequency_thz=1.0, wavemeter_channel=1, relax_time_s=0.0).run(ctx)
    assert shutter.events == [] and mx.trace_calls == []


# -- running a sweep ----------------------------------------------------------------------------


def test_sweeps_run_on_the_caller_owned_devices_and_never_build_their_own(monkeypatch):
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
    assert frequency.run_metadata["status"] == "completed"
    assert (
        frequency.run_metadata["run_id"]
        and frequency.run_metadata["config"]["center_hz"] == 1.5e6
    )

    monkeypatch.setattr(
        "iyzee.experiment.procedures.bandwidth_sweep_steps", lambda rbw_values_hz=None: [BoomStep()]
    )
    with pytest.raises(RuntimeError, match="boom"):  # a failure propagates; nothing is disconnected
        run_bandwidth_sweep(mx)


def test_trace_acquisition_always_disables_the_trace_and_closes_the_shutter(monkeypatch):
    mx = FakeMXAForTraceAcquisition()
    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_trace(mx, 1)
    assert mx.update_states == [(1, True), (1, False)]

    timed_out = FakeMXATimeout()
    with pytest.raises(TimeoutError, match="did not complete"):
        acquire_trace(timed_out, 1)
    assert timed_out.update_states == [(1, True), (1, False)]

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    plain = FakeMXA()
    assert acquire_pair(plain) == {"squeezing": [1], "shot_noise": [2]}  # no shutter: both traces

    def failing(mx, trace_num):
        raise RuntimeError("sweep failed")

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", failing)
    shutter = FakeShutterControl()
    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_pair(FakeMXA(), shutter)
    assert shutter.events == ["open", "close"]  # closed even though the trace failed

    unavailable = FakeShutterControl(can_open=False)
    with pytest.raises(RuntimeError, match="shutter did not open"):
        acquire_pair(FakeMXA(), unavailable)
    assert unavailable.events == ["open", "close"]
    assert not unavailable.is_open()
