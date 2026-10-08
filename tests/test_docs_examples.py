"""The example scripts the guide prints are real code: run them against real recordings."""

from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import pytest

from iyzee.devices.scope import Channel, Coupling, TriggerCoupling, TriggerMode, TriggerSlope
from iyzee.experiment import StepResult, save_step_results
from iyzee.scope_workflows import (
    ChannelSettings,
    ScopeAcquisition,
    ScopeWaveform,
    TriggerSettings,
    save_scope_acquisition,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "examples"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IYZEE_DATA_DIR", str(tmp_path))
    month = tmp_path / "2026-01"
    month.mkdir()
    return month


def test_load_sweep_example_reads_what_save_step_results_wrote(data_dir, capsys):
    results = [
        StepResult(
            "rbw=20000Hz", 20e3, "Hz", {"squeezing": [-61.0, -61.0], "shot_noise": [-58.0, -58.0]}
        ),
        StepResult("rbw=40000Hz", 40e3, "Hz", {"squeezing": [-60.0, -60.0]}),
    ]
    save_step_results(
        results, data_dir, {"status": "completed", "failed_steps": []}, name="bandwidth"
    )
    runpy.run_path(str(EXAMPLES / "load_sweep.py"))
    out = capsys.readouterr().out
    assert "completed, 0 failed" in out
    assert "-3.00 dB" in out
    assert "no data" in out  # a point without a shot-noise trace is reported, not invented


def test_load_scope_example_reads_what_save_scope_acquisition_wrote(data_dir, capsys):
    time = np.arange(4, dtype=np.float64) * 1e-6
    values = np.array([0.0, 0.1, 0.0, -0.1])
    wave = ScopeWaveform(
        channel=Channel.C1,
        time=time,
        values=values,
        raw_codes=np.arange(4, dtype=np.int16),
        value_unit="V",
        time_unit="S",
        time_offset=0.0,
        time_interval=1e-6,
        vertical_gain=1e-3,
        vertical_offset=0.0,
        stats={"sample_count": 4, "rms": 0.07, "peak_to_peak": 0.2},
    )
    settings = (ChannelSettings(Channel.C1, True, 0.1, 0.0, Coupling.DC_1M),)
    trigger = TriggerSettings(
        Channel.C1, TriggerMode.NORMAL, TriggerSlope.POSITIVE, TriggerCoupling.DC, 0.0, 1e-6
    )
    acquisition = ScopeAcquisition(
        measurement_id="m",
        started_at_utc="a",
        completed_at_utc="b",
        instrument_address="x",
        socket_timeout_s=1.0,
        requested_channel_settings=settings,
        requested_trigger_settings=trigger,
        applied_channel_settings=settings,
        applied_trigger_settings=trigger,
        waveforms=(wave,),
        instrument_id="LECROY,TEST",
        warnings=("could not freeze",),
    )
    save_scope_acquisition(acquisition, data_dir)
    runpy.run_path(str(EXAMPLES / "load_scope.py"))
    out = capsys.readouterr().out
    assert "LECROY,TEST (VISA (VXI-11))" in out
    assert "could not freeze" in out
    assert "C1: 4 samples" in out


def test_detuning_example_reports_distance_to_the_nearest_rubidium_line(data_dir, capsys):
    from iyzee.devices.wavemeter import Rb_transitions

    label, line = next(t for t in Rb_transitions if t[0] == "D1 - Rb87_F22")
    measured = line + 5e-6  # 5 MHz above the line
    results = [
        StepResult(
            "freq",
            line,
            "THz",
            {"squeezing": [-61.0, -61.0], "shot_noise": [-58.0, -58.0]},
            {"measured_frequency_thz": measured},
        )
    ]
    save_step_results(
        results, data_dir, {"status": "completed", "failed_steps": []}, name="frequency"
    )
    runpy.run_path(str(EXAMPLES / "detuning.py"))
    out = capsys.readouterr().out
    assert f"from {label}" in out
    assert "+5.0 MHz" in out
    assert "-3.00 dB" in out


def test_the_readme_library_snippet_runs(tmp_path, monkeypatch):
    """The 'Use it as a library' block in the README is real: run it with a fake analyzer."""
    import re

    import iyzee.devices.mxa as mxa_module
    import iyzee.experiment as experiment
    import iyzee.experiment.io as io_module

    readme = (EXAMPLES.parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Use it as a library", 1)[1]
    block = re.search(r"~~~python\n(.*?)~~~", section, re.S)
    assert block, "README lost its library example"

    results = [StepResult("rbw=1Hz", 1.0, "Hz", {"squeezing": [-61.0], "shot_noise": [-58.0]})]

    class FakeMXA:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(mxa_module, "KeysightMXA", FakeMXA)
    monkeypatch.setattr(experiment, "run_bandwidth_sweep", lambda mx: results)
    monkeypatch.setattr(io_module, "DATA_ROOT", tmp_path)

    namespace: dict[str, object] = {}
    exec(block.group(1), namespace)  # noqa: S102 - our own README, run against fakes
    assert namespace["results"] == results
    assert list(tmp_path.rglob("*.npz")), "the snippet did not save a recording"
