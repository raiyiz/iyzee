"""Tests for iyzee.waveform_math: pure Trace operations and figure building.

Most tests build a Recording by hand (fast, precise control over the exact
schema saved by scope_workflows.save_scope_acquisition); one end-to-end test
goes through the real acquire -> save -> load -> traces_from_scope_recording
path to catch any schema drift between the writer and this module's reader.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
from test_scope_workflows import FakeScope

from iyzee.devices.scope import Channel
from iyzee.experiment.io import Recording, load_recording
from iyzee.scope_workflows import acquire_scope_recording, save_scope_acquisition
from iyzee.waveform_math import (
    Trace,
    build_waveform_figure,
    save_waveform_figure,
    scale_trace,
    subtract_background,
    subtract_traces,
    traces_from_scope_recording,
)


def _trace(label: str, time, values, *, time_unit="S", value_unit="V", **meta) -> Trace:
    return Trace(
        label=label,
        time=np.asarray(time, dtype=np.float64),
        values=np.asarray(values, dtype=np.float64),
        time_unit=time_unit,
        value_unit=value_unit,
        meta=meta,
    )


def _recording(waveforms: list[dict], applied: list[dict] | None = None, **extra) -> Recording:
    """A hand-built Recording matching save_scope_acquisition's schema."""
    arrays: dict[str, np.ndarray] = {}
    manifest_waveforms = []
    for w in waveforms:
        channel = w["channel"]
        arrays[f"time_{channel}"] = np.asarray(w["time"], dtype=np.float64)
        arrays[f"value_{channel}"] = np.asarray(w["value"], dtype=np.float64)
        manifest_waveforms.append(
            {
                "channel": channel,
                "value_unit": w.get("value_unit", "V"),
                "time_unit": w.get("time_unit", "S"),
            }
        )
    metadata = {
        "waveforms": manifest_waveforms,
        "configuration": {"applied_channel_settings": applied or []},
        **extra,
    }
    return Recording(path=Path("fake.npz"), arrays=arrays, metadata=metadata)


# -- traces_from_scope_recording ------------------------------------------------------------


def test_trace_math_converts_subtracts_corrects_and_scales():
    # -- scope recordings become traces with units and applied settings
    recording = _recording(
        [
            {
                "channel": "C1",
                "time": [0.0, 1.0],
                "value": [1.0, 2.0],
                "value_unit": "A",
                "time_unit": "S",
            },
            {"channel": "C3", "time": [0.0, 1.0], "value": [3.0, 4.0]},
        ],
        applied=[{"channel": "C1", "volts_per_div": 0.5, "coupling": "D1M"}],
    )

    c1, c3 = traces_from_scope_recording(recording)

    assert [c1.label, c3.label] == ["C1", "C3"]
    np.testing.assert_array_equal(c1.values, [1.0, 2.0])
    assert c1.value_unit == "A" and c1.time_unit == "S"  # from the manifest entry
    assert c1.meta["volts_per_div"] == 0.5 and c1.meta["coupling"] == "D1M"  # legend metadata
    assert "volts_per_div" not in c3.meta  # no applied-settings entry: still loads

    del recording.arrays["value_C1"]  # a channel missing its array is skipped, not raised
    assert [t.label for t in traces_from_scope_recording(recording)] == ["C3"]
    other = Recording(path=Path("fake.npz"), arrays={}, metadata={"kind": "sweep"})
    assert traces_from_scope_recording(other) == []  # not a scope recording: no traces

    # -- subtract traces is exact resamples and never mutates or mixes units
    a = _trace("C1", [0.0, 1.0, 2.0], [5.0, 6.0, 7.0])
    b = _trace("C2", [0.0, 1.0, 2.0], [1.0, 1.0, 1.0])
    a_before, b_before = a.values.copy(), b.values.copy()

    result = subtract_traces(a, b)

    np.testing.assert_array_equal(result.values, [4.0, 5.0, 6.0])
    assert result.label == "C1 - C2" and result.time is a.time  # a's grid is kept
    assert result.time_unit == a.time_unit and result.value_unit == a.value_unit
    np.testing.assert_array_equal(a.values, a_before)
    np.testing.assert_array_equal(b.values, b_before)

    flat = _trace("C1", [0.0, 1.0, 2.0], [10.0, 10.0, 10.0])
    ramp = _trace("C2", [0.0, 2.0], [0.0, 4.0])  # linear 0->4 over 0..2, resampled onto flat's grid
    np.testing.assert_allclose(subtract_traces(flat, ramp).values, [10.0, 8.0, 6.0])

    amps = _trace("C2", [0.0], [1.0], value_unit="A")
    volts = _trace("C1", [0.0], [1.0], value_unit="V")
    for operation in (subtract_traces, lambda x, y: subtract_background(x, reference=y)):
        with pytest.raises(ValueError, match="different units"):
            operation(volts, amps)

    # -- background correction by region or reference and its input checks
    trace = _trace("C1", [0.0, 1.0, 2.0, 3.0], [10.0, 12.0, 100.0, 100.0])

    result = subtract_background(trace, region=(0.0, 1.0))  # mean of [10, 12] == 11
    np.testing.assert_allclose(result.values, [-1.0, 1.0, 89.0, 89.0])
    assert "bg-corrected" in result.label
    backward = subtract_background(trace, region=(1.0, 0.0))  # bounds in either order
    np.testing.assert_array_equal(result.values, backward.values)
    with pytest.raises(ValueError, match="no samples"):
        subtract_background(trace, region=(5.0, 6.0))

    flat = _trace("C1", [0.0, 1.0, 2.0], [10.0, 10.0, 10.0])
    dark = _trace("dark", [0.0, 2.0], [0.0, 2.0])  # interpolates to [0, 1, 2]
    np.testing.assert_allclose(subtract_background(flat, reference=dark).values, [10.0, 9.0, 8.0])

    for kwargs in ({}, {"region": (0.0, 1.0), "reference": dark}):  # exactly one mode
        with pytest.raises(ValueError, match="exactly one"):
            subtract_background(flat, **kwargs)

    # -- scale trace is affine defaults to identity and refuses bad scales
    trace = _trace("C1", [0.0, 1.0, 2.0], [1.0, 2.0, 3.0])

    result = scale_trace(trace, x_scale=2.0, x_offset=1.0, y_scale=1000.0, y_offset=-5.0)
    np.testing.assert_allclose(result.time, [1.0, 3.0, 5.0])
    np.testing.assert_allclose(result.values, [995.0, 1995.0, 2995.0])
    assert "x*2" in result.label and "y*1000" in result.label

    same = scale_trace(trace)
    np.testing.assert_array_equal(same.values, trace.values)
    assert same.label == trace.label  # no transform applied -> no change noted

    for kwargs, message in (
        ({"x_scale": 0.0}, "nonzero"),
        ({"y_scale": 0.0}, "nonzero"),
        ({"x_scale": float("nan")}, "finite"),
    ):
        with pytest.raises(ValueError, match=message):
            scale_trace(trace, **kwargs)


# -- subtract_traces / subtract_background ----------------------------------------------------


# -- figures -----------------------------------------------------------------------------------


@contextlib.contextmanager
def _figure(traces, **kwargs):
    """A built waveform figure, always closed afterwards."""
    fig = build_waveform_figure(traces, **kwargs)
    try:
        yield fig
    finally:
        plt.close(fig)


def test_waveform_figures_label_axes_and_save_a_real_png(tmp_path):
    # -- waveform figures label lines and axes without guessing units
    traces = [
        _trace("C1", [0.0, 1.0], [1.0, 2.0], volts_per_div=0.5, coupling="D1M"),
        _trace("C2", [0.0, 1.0], [3.0, 4.0]),
    ]
    with _figure(traces, title="My Run") as fig:
        (ax,) = fig.axes
        labels = [line.get_label() for line in ax.lines]
        assert len(labels) == 2 and ax.get_legend() is not None and labels[1] == "C2"
        assert "0.5 V/div" in labels[0] and "D1M" in labels[0]  # provenance in the legend
        assert (ax.get_xlabel(), ax.get_ylabel(), ax.get_title()) == (
            "Time (S)",
            "Signal (V)",
            "My Run",
        )

    mixed = [_trace("C0", [0.0], [1.0], value_unit="V"), _trace("C1", [0.0], [1.0], value_unit="A")]
    with _figure(mixed) as fig:
        assert fig.axes[0].get_ylabel() == "Signal (mixed units)"
    with _figure([]) as fig:  # nothing to draw does not crash
        assert len(fig.axes[0].lines) == 0

    # -- save waveform figure writes a real png and closes the figure
    traces = [_trace("C1", [0.0, 1.0, 2.0], [1.0, 2.0, 1.0])]
    open_before = len(plt.get_fignums())

    out = save_waveform_figure(traces, tmp_path / "plot.png", title="Test")
    save_waveform_figure(traces, tmp_path / "plot2.png")

    assert out == tmp_path / "plot.png"
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # a real matplotlib-rendered image
    assert len(plt.get_fignums()) == open_before  # no leaked figures after repeated saves


def test_end_to_end_acquire_save_load_and_subtract(tmp_path):
    scope = FakeScope()

    recording = acquire_scope_recording(scope, [Channel.C1, Channel.C2])
    stem = save_scope_acquisition(recording, tmp_path)
    loaded = load_recording(stem.with_suffix(".npz"))
    traces = traces_from_scope_recording(loaded)

    by_label = {t.label: t for t in traces}
    assert set(by_label) == {"C1", "C2"}

    difference = subtract_traces(by_label["C1"], by_label["C2"])
    np.testing.assert_allclose(difference.values, by_label["C1"].values - by_label["C2"].values)
