import itertools
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Literal, NoReturn

import pytest

from iyzee.experiment.core import ExperimentContext
from iyzee.experiment.procedures import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    Analyzer,
    AnalyzerConfig,
    BandwidthStep,
    FrequencyStep,
    Sweep,
    SweepStep,
    TraceAcquisition,
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
    def __init__(self) -> None:
        self.rbw_values: list[float | None] = []
        self.vbw_values: list[tuple[float | None, bool]] = []
        self.trace_calls: list[int] = []

    def set_rbw(self, rbw_hz: float | None = None, auto: bool = False) -> None:
        self.rbw_values.append(rbw_hz)

    def set_vbw(self, vbw_hz: float | None = None, auto: bool = False) -> None:
        self.vbw_values.append((vbw_hz, auto))

    def set_center_freq(self, freq_hz: float) -> None:
        pass

    def set_span(self, span_hz: float) -> None:
        pass

    def set_attenuation_auto(self, state: bool = True) -> None:
        pass

    def set_trigger_source(self, source: str) -> None:
        pass

    def set_sweep_duration(self, time: int) -> None:
        pass

    def set_average_count(self, count: int) -> None:
        pass

    def set_average_type(self, avg_type: str) -> None:
        pass

    def set_trace_display(self, trace_num: int | str, state: bool = True) -> None:
        pass

    def set_trace_mode(self, trace_num: int | str, mode: str) -> None:
        pass

    def set_trace_update(self, trace_num: int, state: bool = True) -> None:
        self.trace_calls.append(trace_num if state else -trace_num)

    def single_sweep_wait(self, timeout_sec: float = 30.0) -> bool:
        return True

    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]:
        return [float(trace_num)]

def fake_acquire_trace(mx: FakeMXA, trace_num: int) -> list[float]:
    mx.trace_calls.append(trace_num)
    return [float(trace_num)]

class BoomStep:
    label = "boom"

    def run(self, ctx: ExperimentContext) -> NoReturn:
        raise RuntimeError("boom")

class FakeShutterControl:
    """Stand-in for power.ShutterControl as a context manager."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def __enter__(self) -> Any:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object | None) -> Literal[False]:
        return False

    def open(self) -> None:
        self.events.append("open")

    def close(self) -> None:
        self.events.append("close")

class FakeMXAForTraceAcquisition:
    def __init__(self) -> None:
        self.update_states: list[tuple[int, bool]] = []

    def set_trace_update(self, trace_num: int, state: bool = True) -> None:
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self, timeout_sec: float = 30.0) -> NoReturn:
        raise RuntimeError("sweep failed")

    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]:
        return [float(trace_num)]

def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

def _wavemeter(events: list[tuple[object, ...]], frequency: float = 377.1) -> Any:
    def set_pid_setpoint(freq: float, channel: int) -> None:
        events.append(("set", freq, channel))

    def read_frequency(channel: int) -> float:
        events.append(("read", channel))
        return frequency

    return SimpleNamespace(
        set_pid_setpoint=set_pid_setpoint,
        read_frequency=read_frequency,
    )

# -- builders and defaults ----------------------------------------------------------------------

def test_the_builders_and_their_defaults() -> None:
    steps = bandwidth_sweep_steps()
    assert [s.rbw_hz for s in steps] == [20e3 * i for i in range(1, 20)]

    sweep = build_bandwidth_sweep([20e3, 40e3], sweep_duration_ms=10)
    assert sweep.kind == "bandwidth" and [step.rbw_hz for step in sweep.steps] == [20e3, 40e3]
    config = sweep.config
    assert (config.avg_count, config.sweep_duration_ms) == (200, 10)
    assert (config.res_bw_hz, config.trig_source) == (24e3, "IMM")

    # The frequency scan settles for the laser, not for the analyzer's workload.
    frequency_sweep = build_frequency_sweep(
        laser_center_thz=377.1,
        wavemeter_channel=4,
        offsets_thz=[-1e-9, 0.0, 1e-9],
        sweep_duration_ms=10,
    )
    assert frequency_sweep.kind == "frequency"
    assert [step.frequency_thz for step in frequency_sweep.steps] == pytest.approx(
        [377.099999999, 377.1, 377.100000001]
    )
    assert [step.relax_time_s for step in frequency_sweep.steps] == [0.5, 0.5, 0.5]
    assert frequency_sweep.config.avg_count == 150
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

def test_every_sweep_kind_is_a_step_that_names_itself_and_declares_what_it_needs() -> None:
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

def test_a_bandwidth_step_sets_vbw_to_twice_rbw_and_ignores_the_shutter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    mx, shutter = FakeMXA(), FakeShutterControl()
    ctx = ExperimentContext(mx=mx, run_id="t", shutter=shutter)

    result = BandwidthStep(rbw_hz=1000).run(ctx)

    assert mx.rbw_values == [1000] and mx.vbw_values == [(2000, False)]
    assert (result.x_value, result.x_unit) == (1000, "Hz")
    assert result.traces == {"squeezing": [1], "shot_noise": [2]}
    assert result.meta == {"rbw_hz": 1000, "vbw_hz": 2000}
    assert shutter.events == []

def test_a_frequency_step_goes_there_settles_reads_then_records_with_the_shutter(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.time.sleep", lambda s: events.append(("sleep", s))
    )
    def fake_acquire(mx: TraceAcquisition, trace_num: int) -> list[float]:
        events.append(("acquire", trace_num))
        return [float(trace_num)]

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire)
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

def test_a_frequency_step_names_what_the_context_lacks_and_touches_nothing_without_it() -> None:
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

def test_sweeps_run_on_the_caller_owned_devices_and_never_build_their_own(monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet(monkeypatch)
    prepared: list[tuple[object, AnalyzerConfig]] = []
    def prepare(analyzer: Analyzer, traces: Sequence[int], config: AnalyzerConfig | None = None) -> Analyzer:
        prepared.append((analyzer, config or AnalyzerConfig()))
        return analyzer

    monkeypatch.setattr("iyzee.experiment.procedures.prepare_analyzer", prepare)
    monkeypatch.setattr(
        "iyzee.experiment.procedures.Wavemeter",
        lambda *a, **k: pytest.fail("a client was built although one was passed"),
        raising=False,
    )
    mx, shutter = FakeMXA(), FakeShutterControl()
    events: list[tuple[object, ...]] = []

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

def test_trace_acquisition_always_disables_the_trace_and_closes_the_shutter(monkeypatch: pytest.MonkeyPatch) -> None:
    mx = FakeMXAForTraceAcquisition()
    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_trace(mx, 1)
    assert mx.update_states == [(1, True), (1, False)]

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    plain = FakeMXA()
    assert acquire_pair(plain) == {"squeezing": [1], "shot_noise": [2]}  # no shutter: both traces

    def failing(mx: TraceAcquisition, trace_num: int) -> NoReturn:
        raise RuntimeError("sweep failed")

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", failing)
    shutter = FakeShutterControl()
    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_pair(FakeMXA(), shutter)
    assert shutter.events == ["open", "close"]  # closed even though the trace failed
