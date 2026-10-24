import itertools
from types import SimpleNamespace

import pytest

from iyzee.devices.base import InstrumentError
from iyzee.devices.power import ShutterError
from iyzee.experiment.core import AcquisitionError, ExperimentContext, StepResult
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
    check_shutter_ready,
    frequency_sweep_config,
    frequency_sweep_steps,
    prepare_analyzer,
    run_bandwidth_sweep,
    run_frequency_sweep,
    run_sweep,
)


class FakeMXA:
    """Records what is set; like the instrument it queues errors rather than refusing."""

    def __init__(self):
        self.rbw_values = []
        self.vbw_values = []
        self.trace_calls = []
        self.errors = []  # what SYST:ERR? would report next
        self.rounded_rbw = None  # if set, the analyzer "rounds" the bandwidth to this

    def set_rbw(self, rbw_hz):
        self.rbw_values.append(rbw_hz)

    def set_vbw(self, vbw_hz, auto=False):
        self.vbw_values.append((vbw_hz, auto))

    def get_errors(self):
        errors, self.errors = self.errors, []
        return errors

    def check_errors(self, context=""):
        if errors := self.get_errors():
            raise InstrumentError(f"the analyzer reported {'; '.join(errors)} while {context}")

    def read_back(self):
        rbw = self.rounded_rbw or (self.rbw_values[-1] if self.rbw_values else 1e4)
        vbw = self.vbw_values[-1][0] if self.vbw_values else 2e4
        return {
            "rbw_hz": float(rbw),
            "vbw_hz": float(vbw),
            "sweep_time_s": 0.01,
            "avg_count": 100,
            "avg_type": "LOG",
        }


def fake_acquire_trace(mx, trace_num):
    mx.trace_calls.append(trace_num)
    return [trace_num]


class BoomStep:
    label = "boom"

    def run(self, ctx):
        raise RuntimeError("boom")


class FakeShutterControl:
    """Stand-in for power.ShutterControl as a context manager.

    Models what the supply reports: the channel is live only if it was armed *and* the
    master output is on, and ``stuck_open`` makes ``close()`` have no effect.
    """

    def __init__(self, master_on=True, stuck_open=False):
        self.events = []
        self.master_on = master_on
        self.stuck_open = stuck_open
        self.armed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def open(self):
        self.events.append("open")
        self.armed = True

    def close(self):
        self.events.append("close")
        if not self.stuck_open:
            self.armed = False

    def is_open(self):
        return self.armed and self.master_on

    def master_output_on(self):
        return self.master_on

    def close_verified(self):
        """The same contract as ShutterControl.close_verified (tested against a supply)."""
        self.close()
        if self.is_open():
            self.close()
            if self.is_open():
                raise ShutterError("the shutter is still powered after close; check the supply")


class FakeMXAForTraceAcquisition:
    def __init__(self):
        self.update_states = []

    def set_trace_update(self, trace_num, state):
        self.update_states.append((trace_num, state))

    def single_sweep_wait(self):
        raise RuntimeError("sweep failed")


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
    assert {k: result.meta[k] for k in ("rbw_hz", "vbw_hz")} == {"rbw_hz": 1000, "vbw_hz": 2000}
    assert result.meta["analyzer"]["rbw_hz"] == 1000.0  # and what the analyzer reports using
    assert shutter.events == []


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
        ("acquire", 1),
        ("acquire", 2),
    ]
    assert shutter.events == ["open", "close"]  # opened only for the squeezing trace
    assert result.meta["measured_frequency_thz"] == pytest.approx(377.100002)


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


# -- a measurement that cannot be trusted is an error, not data ---------------------------------


class TimedOutMXA(FakeMXAForTraceAcquisition):
    """An analyzer whose sweep wait times out (``single_sweep_wait`` returns False)."""

    def __init__(self):
        super().__init__()
        self.read = False

    def single_sweep_wait(self):
        return False

    def get_trace_data(self, trace_num=1, binary=True):
        self.read = True
        return [-80.0]


def test_a_sweep_that_did_not_complete_is_an_error_and_its_data_is_never_read():
    mx = TimedOutMXA()
    with pytest.raises(AcquisitionError, match="did not report its sweep complete"):
        acquire_trace(mx, 1)
    assert not mx.read  # the partial/stale trace is not fetched
    assert mx.update_states == [(1, True), (1, False)]  # the trace is still released


def test_a_shutter_that_does_not_really_open_stops_the_pair_before_any_trace(monkeypatch):
    taken = []
    monkeypatch.setattr(
        "iyzee.experiment.procedures.acquire_trace",
        lambda mx, n: taken.append(n) or [n],
    )
    shutter = FakeShutterControl(master_on=False)  # arming it powers nothing
    with pytest.raises(AcquisitionError, match="master output"):
        acquire_pair(FakeMXA(), shutter)
    assert taken == []  # nothing was measured with the light path closed
    assert shutter.events == ["open", "close"]  # and it is left closed


def test_a_shutter_that_will_not_close_is_retried_once_then_reported(monkeypatch):
    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", fake_acquire_trace)
    shutter = FakeShutterControl(stuck_open=True)
    with pytest.raises(AcquisitionError, match="still powered after close"):
        acquire_pair(FakeMXA(), shutter)
    assert shutter.events == ["open", "close", "close"]  # one retry, no more


def test_check_shutter_ready_passes_without_a_shutter_and_with_the_master_on():
    check_shutter_ready(None)
    check_shutter_ready(FakeShutterControl(master_on=True))
    with pytest.raises(AcquisitionError, match="master output is off"):
        check_shutter_ready(FakeShutterControl(master_on=False))


def test_run_sweep_refuses_to_start_with_the_master_output_off():
    mx = FakeMXA()
    shutter = FakeShutterControl(master_on=False)
    with pytest.raises(AcquisitionError, match="master output is off"):
        run_frequency_sweep(mx, shutter, offsets_thz=[0.0], wavemeter=_wavemeter([]))
    assert mx.rbw_values == [] and shutter.events == []  # nothing was configured or driven


# -- the analyzer's own account of what it is doing --------------------------------------------


def test_setup_discards_old_errors_and_fails_on_a_setting_the_analyzer_rejected(caplog):
    mx = FakeMXA()
    for name in [
        "set_center_freq",
        "set_span",
        "set_attenuation_auto",
        "set_trigger_source",
        "set_sweep_duration",
        "set_average_count",
        "set_average_type",
        "set_trace_display",
        "set_trace_mode",
    ]:
        setattr(mx, name, lambda *a, **k: None)

    mx.errors = ['-113,"Undefined header"']  # left over from before this run
    with caplog.at_level("WARNING", logger="iyzee.experiment"):
        prepare_analyzer(mx, (1, 2))  # does not raise: stale entries are not this run's
    assert "discarding 1 analyzer error" in caplog.text

    real_set_rbw = mx.set_rbw

    def rejected(rbw_hz):
        real_set_rbw(rbw_hz)
        mx.errors.append('-222,"Data out of range"')

    mx.set_rbw = rejected
    with pytest.raises(InstrumentError, match=r"-222.*configuring the analyzer"):
        prepare_analyzer(mx, (1, 2))


def test_each_point_records_what_the_analyzer_is_really_using(monkeypatch):
    _quiet(monkeypatch)
    mx = FakeMXA()
    mx.rounded_rbw = 24_000.0

    result = BandwidthStep(rbw_hz=24_123.0).run(ExperimentContext(mx=mx, run_id="t"))

    assert result.x_value == 24_123.0  # the request is the axis...
    assert result.meta["analyzer"]["rbw_hz"] == 24_000.0  # ...and the truth is kept beside it
    assert result.meta["rbw_hz"] == 24_123.0


def test_a_large_rounding_of_the_bandwidth_is_logged(monkeypatch, caplog):
    _quiet(monkeypatch)
    mx = FakeMXA()
    mx.rounded_rbw = 30_000.0
    with caplog.at_level("WARNING", logger="iyzee.experiment"):
        BandwidthStep(rbw_hz=20_000.0).run(ExperimentContext(mx=mx, run_id="t"))
    assert "asked for RBW 20000 Hz, the analyzer is using 30000 Hz" in caplog.text

    caplog.clear()
    mx.rounded_rbw = 20_100.0  # within 1 %: not worth a line
    with caplog.at_level("WARNING", logger="iyzee.experiment"):
        BandwidthStep(rbw_hz=20_000.0).run(ExperimentContext(mx=mx, run_id="t"))
    assert "asked for RBW" not in caplog.text


def test_a_setting_the_analyzer_rejected_fails_the_point_before_any_trace_is_taken(monkeypatch):
    _quiet(monkeypatch)
    mx = FakeMXA()
    real_set_vbw = mx.set_vbw
    mx.set_vbw = lambda *a, **k: (real_set_vbw(*a, **k), mx.errors.append("-221,Settings conflict"))
    with pytest.raises(InstrumentError, match=r"-221.*setting up rbw=20000Hz"):
        BandwidthStep(rbw_hz=20_000.0).run(ExperimentContext(mx=mx, run_id="t"))
    assert mx.trace_calls == []  # nothing was measured under the old setting


def test_an_error_raised_during_the_acquisition_fails_the_point(monkeypatch):
    mx = FakeMXA()
    monkeypatch.setattr("iyzee.experiment.procedures.time.sleep", lambda s: None)

    def acquire_then_complain(mx, trace_num):
        mx.errors.append("-230,Data corrupt or stale")
        return [trace_num]

    monkeypatch.setattr("iyzee.experiment.procedures.acquire_trace", acquire_then_complain)
    with pytest.raises(InstrumentError, match=r"-230.*measuring rbw=20000Hz"):
        BandwidthStep(rbw_hz=20_000.0).run(ExperimentContext(mx=mx, run_id="t"))


def test_a_scripted_run_carries_its_record_and_saving_it_stores_it(monkeypatch, tmp_path):
    import json

    from iyzee.experiment import save_step_results

    _quiet(monkeypatch)
    mx = FakeMXA()
    for name in (
        "set_center_freq set_span set_attenuation_auto set_trigger_source set_sweep_duration "
        "set_average_count set_average_type set_trace_display set_trace_mode"
    ).split():
        setattr(mx, name, lambda *a, **k: None)

    results = run_bandwidth_sweep(mx, [20_000.0, 40_000.0], avg_count=7)

    assert isinstance(results, list) and len(results) == 2  # still a plain list of results
    assert results.record.status == "completed" and results.record.config["avg_count"] == 7
    sidecar = json.loads(save_step_results(results, tmp_path).with_suffix(".json").read_text())
    assert sidecar["run_metadata"]["run_id"] == results.record.run_id
    assert sidecar["run_metadata"]["config"]["avg_count"] == 7
    assert sidecar["run_metadata"]["status"] == "completed"


def test_an_explicit_run_metadata_still_wins_over_the_attached_record(tmp_path):
    import json

    from iyzee.experiment import RunRecord, SweepResults, save_step_results

    point = StepResult("rbw=1Hz", 1.0, "Hz", {"squeezing": [1.0], "shot_noise": [2.0]}, {})
    results = SweepResults([point], record=RunRecord(run_id="from-record"))
    path = save_step_results(results, tmp_path, run_metadata={"run_id": "explicit"})
    assert json.loads(path.with_suffix(".json").read_text())["run_metadata"] == {"run_id": "explicit"}
