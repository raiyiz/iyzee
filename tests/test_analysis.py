"""``iyzee.analysis``: what a loaded recording means, independent of any screen."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iyzee.analysis import summarize_scope, summarize_sweep
from iyzee.experiment import StepResult, load_recording, save_step_results
from iyzee.experiment.io import Recording
from iyzee.waveform_math import Trace


def _saved_sweep(tmp_path: Path, results: list[StepResult], *, name: str) -> Recording:
    path = save_step_results(results, tmp_path, {"status": "completed"}, name=name)
    return load_recording(path)


def test_sweep_summaries_plot_the_right_axis_and_honour_the_statistic(tmp_path):
    # -- a bandwidth sweep reports requested setpoints and relative noise
    recording = _saved_sweep(
        tmp_path,
        [
            StepResult(
                "rbw=20k", 20e3, "Hz", {"squeezing": [-61.0, -61.0], "shot_noise": [-58.0, -58.0]}
            ),
            StepResult("rbw=40k", 40e3, "Hz", {"squeezing": [-60.0, -60.0]}),
        ],
        name="bandwidth",
    )

    sweep = summarize_sweep(recording)

    assert sweep.axis == "bandwidth"
    assert sweep.x_unit == "Hz"
    assert [point.requested for point in sweep.points] == [20e3, 40e3]
    assert [point.measured for point in sweep.points] == [None, None]
    assert sweep.points[0].relative_noise_db == pytest.approx(-3.0)
    # A point with no shot-noise trace has no relative noise; it is reported as missing, not zero.
    assert sweep.points[1].relative_noise_db is None
    assert sweep.points[0].x == 20e3

    # -- a frequency sweep plots at the measured frequency
    recording = _saved_sweep(
        tmp_path,
        [
            StepResult(
                "f0",
                377.1,
                "THz",
                {"squeezing": [-61.0], "shot_noise": [-58.0]},
                {"wavemeter_channel": 4, "measured_frequency_thz": 377.1000042},
            ),
            StepResult(
                "f1",
                377.2,
                "THz",
                {"squeezing": [-60.0], "shot_noise": [-58.0]},
                {"wavemeter_channel": 4, "measured_frequency_thz": float("nan")},
            ),
        ],
        name="frequency",
    )

    sweep = summarize_sweep(recording)

    assert sweep.axis == "frequency"
    assert sweep.x_unit == "THz"
    assert sweep.points[0].measured == pytest.approx(377.1000042)
    assert sweep.points[0].x == pytest.approx(377.1000042)
    # An unusable reading falls back to the setpoint for plotting but is not reported as measured.
    assert sweep.points[1].measured is None
    assert sweep.points[1].x == 377.2


def _scope_recording(**metadata) -> Recording:
    return Recording(Path("scope.npz"), {}, metadata)


def _trace(label: str) -> Trace:
    return Trace(label, np.zeros(2), np.zeros(2), "S", "V")


def test_scope_summaries_collect_identity_stats_and_timebases():
    # -- a scope summary collects identity stats and timebase
    waveform = {
        "channel": "C1",
        "time_offset": -5e-6,
        "time_interval": 1e-9,
        "time_unit": "S",
        "stats": {"sample_count": 10, "min": -0.1, "max": 0.1, "peak_to_peak": 0.2, "rms": 0.07},
    }
    recording = _scope_recording(
        measurement_id="m1",
        started_at_utc="2026-01-01T00:00:00Z",
        instrument={"address": "10.0.0.2"},
        waveforms=[waveform],
        configuration={"applied_trigger_settings": {"time_per_div": 1e-6}},
    )

    summary = summarize_scope(recording, [_trace("C1")])

    assert summary.measurement_id == "m1"
    assert summary.started_at_utc == "2026-01-01T00:00:00Z"
    assert summary.instrument_address == "10.0.0.2"
    assert summary.time_per_div == 1e-6
    (stats,) = summary.channels
    assert (stats.channel, stats.value_unit, stats.sample_count) == ("C1", "V", 10)
    assert (stats.minimum, stats.maximum, stats.peak_to_peak, stats.rms) == (-0.1, 0.1, 0.2, 0.07)
    (timebase,) = summary.timebases
    assert timebase.channels == ("C1",)
    assert (timebase.offset, timebase.interval, timebase.samples) == (-5e-6, 1e-9, 10)
