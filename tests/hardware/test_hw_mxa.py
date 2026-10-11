"""The Keysight MXA, for real: identity, error queue, trace transfer, sweeps.

Read-only tests come first; the rest are marked ``actuates`` (they configure the analyzer
and sweep it). Every test runs between a snapshot and a restore of the settings the app
touches (see ``hw_state.MXA_SETTINGS``), so the analyzer is left as it was found.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from iyzee.devices.handles import InstrumentHandle
from iyzee.devices.mxa import KeysightMXA
from iyzee.experiment import (
    TRACE_SHOT,
    TRACE_SQZ,
    AnalyzerConfig,
    load_recording,
    prepare_analyzer,
    run_bandwidth_sweep,
    save_step_results,
)

from .hw_state import MXA_SETTINGS, restore, snapshot

pytestmark = pytest.mark.mxa


@pytest.fixture(autouse=True)
def _restore_analyzer(mx: KeysightMXA, mxa_handle: InstrumentHandle) -> Iterator[None]:
    with mxa_handle.lock:
        mx.get_errors()  # start from a clean error queue
        before = snapshot(mx.query, MXA_SETTINGS)
    yield
    with mxa_handle.lock:
        failures = restore(mx.write, MXA_SETTINGS, before)
        errors = mx.get_errors()
    if failures or errors:
        pytest.fail(
            "could not restore the analyzer's settings\n"
            f"  write failures: {failures or 'none'}\n  SCPI errors: {errors or 'none'}",
            pytrace=False,
        )


# -- read-only ------------------------------------------------------------------------------


def test_identifies_as_a_keysight_analyzer(mx: KeysightMXA) -> None:
    fields = [field.strip() for field in mx.idn().split(",")]
    assert len(fields) == 4, f"*IDN? should be make,model,serial,firmware; got {fields}"
    assert any(maker in fields[0].lower() for maker in ("keysight", "agilent")), fields


def test_error_queue_can_be_drained_and_stays_empty(mx: KeysightMXA) -> None:
    mx.get_errors()
    assert mx.get_errors() == []


def test_the_trace_on_screen_matches_the_frequency_axis(mx: KeysightMXA) -> None:
    points = mx.get_sweep_points()
    axis = mx.get_frequency_axis()
    trace = mx.get_trace_data(TRACE_SQZ)
    assert len(axis) == points
    assert len(trace) == points
    assert mx.get_errors() == []


# -- state-changing ---------------------------------------------------------------------------


@pytest.mark.actuates
def test_prepare_analyzer_is_applied_as_requested(mx: KeysightMXA) -> None:
    """The analyzer ends up configured as asked, and says nothing is wrong about it.

    ``prepare_analyzer`` only writes; this is the read-back and error-queue check it does not
    do itself. RBW is compared loosely because the analyzer rounds it to a step it has.
    """
    config = AnalyzerConfig(
        center_hz=1e6, span_hz=0, avg_count=50, sweep_duration_ms=10, res_bw_hz=24e3
    )
    prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), config)

    assert float(mx.query("FREQ:CENT?")) == pytest.approx(1e6)
    assert float(mx.query("FREQ:SPAN?")) == 0
    assert float(mx.query("AVER:COUN?")) == 50
    assert float(mx.query("SWE:TIME?")) == pytest.approx(0.010, rel=0.05)
    assert float(mx.query("BWID?")) == pytest.approx(24e3, rel=0.2)
    assert mx.query("TRIG:SOUR?").strip().upper().startswith("IMM")
    assert mx.get_errors() == []


@pytest.mark.actuates
def test_a_single_sweep_completes_and_fills_the_trace(mx: KeysightMXA) -> None:
    prepare_analyzer(mx, (TRACE_SQZ,), AnalyzerConfig(avg_count=5))
    mx.set_trace_update(TRACE_SQZ, True)
    finished = mx.single_sweep_wait(timeout_sec=60)
    mx.set_trace_update(TRACE_SQZ, False)

    assert finished is True, "*OPC? did not come back: the sweep did not finish"
    trace = np.asarray(mx.get_trace_data(TRACE_SQZ, binary=False))
    assert trace.size == mx.get_sweep_points()
    assert np.isfinite(trace).all()
    assert mx.get_errors() == []


@pytest.mark.actuates
def test_binary_and_ascii_transfers_agree(mx: KeysightMXA) -> None:
    """Pins the byte order and format of the binary transfer against the ASCII one."""
    prepare_analyzer(mx, (TRACE_SQZ,), AnalyzerConfig(avg_count=5))
    mx.set_trace_update(TRACE_SQZ, True)
    assert mx.single_sweep_wait(timeout_sec=60)
    mx.set_trace_update(TRACE_SQZ, False)

    binary = np.asarray(mx.get_trace_data(TRACE_SQZ, binary=True))
    ascii_ = np.asarray(mx.get_trace_data(TRACE_SQZ, binary=False))
    assert binary.shape == ascii_.shape
    assert np.allclose(binary, ascii_, atol=1e-3)


@pytest.mark.actuates
def test_a_bandwidth_sweep_runs_end_to_end_and_round_trips_to_disk(
    mx: KeysightMXA, mxa_handle: InstrumentHandle, tmp_path: Path
) -> None:
    with mxa_handle.lock:
        results = run_bandwidth_sweep(mx, [20e3, 40e3])
        points = mx.get_sweep_points()

    assert [result.label for result in results] == ["rbw=20000Hz", "rbw=40000Hz"]
    for result in results:
        for name in ("squeezing", "shot_noise"):
            trace = np.asarray(result.traces[name])
            assert trace.size == points, f"{result.label} {name}"
            assert np.isfinite(trace).all(), f"{result.label} {name}"
    assert mx.get_errors() == []

    path = save_step_results(results, tmp_path, name="hardware")
    recording = load_recording(path)
    assert recording.arrays["trace_squeezing"].shape == (2, points)
    assert recording.metadata["data_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
