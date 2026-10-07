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


def test_reads_each_channels_time_and_value_arrays():
    recording = _recording(
        [
            {"channel": "C1", "time": [0.0, 1.0], "value": [1.0, 2.0]},
            {"channel": "C2", "time": [0.0, 1.0], "value": [3.0, 4.0]},
        ]
    )

    traces = traces_from_scope_recording(recording)

    assert [t.label for t in traces] == ["C1", "C2"]
    np.testing.assert_array_equal(traces[0].values, [1.0, 2.0])
    np.testing.assert_array_equal(traces[1].values, [3.0, 4.0])


def test_units_come_from_the_waveforms_manifest_entry():
    recording = _recording(
        [{"channel": "C1", "time": [0.0], "value": [1.0], "value_unit": "A", "time_unit": "S"}]
    )

    (trace,) = traces_from_scope_recording(recording)

    assert trace.value_unit == "A" and trace.time_unit == "S"


def test_a_channel_missing_its_value_array_is_skipped_not_raised():
    recording = _recording([{"channel": "C1", "time": [0.0], "value": [1.0]}])
    del recording.arrays["value_C1"]

    assert traces_from_scope_recording(recording) == []


def test_a_non_scope_recording_yields_no_traces_rather_than_erroring():
    recording = Recording(path=Path("fake.npz"), arrays={}, metadata={"kind": "sweep"})

    assert traces_from_scope_recording(recording) == []


def test_applied_channel_settings_become_legend_metadata():
    recording = _recording(
        [{"channel": "C1", "time": [0.0], "value": [1.0]}],
        applied=[{"channel": "C1", "volts_per_div": 0.5, "coupling": "D1M"}],
    )

    (trace,) = traces_from_scope_recording(recording)

    assert trace.meta["volts_per_div"] == 0.5
    assert trace.meta["coupling"] == "D1M"


def test_a_channel_with_no_applied_settings_entry_still_loads():
    recording = _recording([{"channel": "C3", "time": [0.0], "value": [1.0]}], applied=[])

    (trace,) = traces_from_scope_recording(recording)

    assert trace.label == "C3" and "volts_per_div" not in trace.meta


# -- subtract_traces --------------------------------------------------------------------------


def test_subtract_traces_on_a_shared_grid_is_exact():
    a = _trace("C1", [0.0, 1.0, 2.0], [5.0, 6.0, 7.0])
    b = _trace("C2", [0.0, 1.0, 2.0], [1.0, 1.0, 1.0])

    result = subtract_traces(a, b)

    np.testing.assert_array_equal(result.values, [4.0, 5.0, 6.0])
    assert result.label == "C1 - C2"
    assert result.time is a.time  # a's grid is kept, not recomputed
    assert result.time_unit == a.time_unit and result.value_unit == a.value_unit


def test_subtract_traces_resamples_a_mismatched_grid():
    a = _trace("C1", [0.0, 1.0, 2.0], [10.0, 10.0, 10.0])
    b = _trace("C2", [0.0, 2.0], [0.0, 4.0])  # linear 0->4 over 0..2

    result = subtract_traces(a, b)

    np.testing.assert_allclose(result.values, [10.0, 8.0, 6.0])  # b interpolated to [0, 2, 4]


@pytest.mark.parametrize(
    "operation",
    [
        lambda a, b: subtract_traces(a, b),
        lambda a, b: subtract_background(a, reference=b),
    ],
    ids=["subtract", "background-reference"],
)
def test_operations_reject_traces_with_different_units(operation):
    a = _trace("C1", [0.0], [1.0], value_unit="V")
    b = _trace("C2", [0.0], [1.0], value_unit="A")

    with pytest.raises(ValueError, match="different units"):
        operation(a, b)


def test_subtract_traces_does_not_mutate_its_inputs():
    a = _trace("C1", [0.0, 1.0], [5.0, 6.0])
    b = _trace("C2", [0.0, 1.0], [1.0, 1.0])
    a_before, b_before = a.values.copy(), b.values.copy()

    subtract_traces(a, b)

    np.testing.assert_array_equal(a.values, a_before)
    np.testing.assert_array_equal(b.values, b_before)


# -- subtract_background ------------------------------------------------------------------


def test_background_region_subtracts_the_mean_of_the_window():
    trace = _trace("C1", [0.0, 1.0, 2.0, 3.0], [10.0, 12.0, 100.0, 100.0])

    result = subtract_background(trace, region=(0.0, 1.0))  # mean of [10, 12] == 11

    np.testing.assert_allclose(result.values, [-1.0, 1.0, 89.0, 89.0])
    assert "bg-corrected" in result.label


def test_background_region_accepts_bounds_given_in_either_order():
    trace = _trace("C1", [0.0, 1.0, 2.0], [10.0, 12.0, 100.0])

    forward = subtract_background(trace, region=(0.0, 1.0))
    backward = subtract_background(trace, region=(1.0, 0.0))

    np.testing.assert_array_equal(forward.values, backward.values)


def test_background_region_with_no_samples_inside_raises():
    trace = _trace("C1", [0.0, 1.0, 2.0], [10.0, 12.0, 100.0])

    with pytest.raises(ValueError, match="no samples"):
        subtract_background(trace, region=(5.0, 6.0))


def test_background_reference_is_subtracted_and_resampled_like_subtract_traces():
    trace = _trace("C1", [0.0, 1.0, 2.0], [10.0, 10.0, 10.0])
    dark = _trace("dark", [0.0, 2.0], [0.0, 2.0])  # interpolates to [0, 1, 2]

    result = subtract_background(trace, reference=dark)

    np.testing.assert_allclose(result.values, [10.0, 9.0, 8.0])


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"region": (0.0, 1.0), "reference": _trace("dark", [0.0, 1.0], [0.0, 0.0])}],
)
def test_background_correction_requires_exactly_one_mode(kwargs):
    trace = _trace("C1", [0.0, 1.0], [1.0, 1.0])

    with pytest.raises(ValueError, match="exactly one"):
        subtract_background(trace, **kwargs)


# -- scale_trace ----------------------------------------------------------------------------


def test_scale_trace_applies_an_affine_transform_to_both_axes():
    trace = _trace("C1", [0.0, 1.0, 2.0], [1.0, 2.0, 3.0])

    result = scale_trace(trace, x_scale=2.0, x_offset=1.0, y_scale=1000.0, y_offset=-5.0)

    np.testing.assert_allclose(result.time, [1.0, 3.0, 5.0])
    np.testing.assert_allclose(result.values, [995.0, 1995.0, 2995.0])
    assert "x*2" in result.label and "y*1000" in result.label


def test_scale_trace_defaults_are_the_identity():
    trace = _trace("C1", [0.0, 1.0], [3.0, 4.0])

    result = scale_trace(trace)

    np.testing.assert_array_equal(result.time, trace.time)
    np.testing.assert_array_equal(result.values, trace.values)
    assert result.label == trace.label  # no transform applied -> no change noted


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"x_scale": 0.0}, "nonzero"),
        ({"y_scale": 0.0}, "nonzero"),
        ({"x_scale": float("nan")}, "finite"),
    ],
)
def test_scale_trace_rejects_a_zero_or_non_finite_scale(kwargs, message):
    with pytest.raises(ValueError, match=message):
        scale_trace(_trace("C1", [0.0], [1.0]), **kwargs)


@contextlib.contextmanager
def _figure(traces, **kwargs):
    """A built waveform figure, always closed afterwards."""
    fig = build_waveform_figure(traces, **kwargs)
    try:
        yield fig
    finally:
        plt.close(fig)


def test_build_waveform_figure_draws_one_labelled_line_per_trace_with_provenance():
    traces = [
        _trace("C1", [0.0, 1.0], [1.0, 2.0], volts_per_div=0.5, coupling="D1M"),
        _trace("C2", [0.0, 1.0], [3.0, 4.0]),
    ]

    with _figure(traces) as fig:
        (ax,) = fig.axes
        labels = [line.get_label() for line in ax.lines]
        assert len(labels) == 2 and ax.get_legend() is not None
        assert labels[1] == "C2"
        assert "0.5 V/div" in labels[0] and "D1M" in labels[0]


@pytest.mark.parametrize(
    "units,ylabel",
    [(("V",), "Signal (V)"), (("V", "A"), "Signal (mixed units)")],
    ids=["shared-unit", "mixed-units"],
)
def test_build_waveform_figure_labels_axes_without_guessing_units(units, ylabel):
    traces = [_trace(f"C{i}", [0.0], [1.0], value_unit=unit) for i, unit in enumerate(units)]

    with _figure(traces, title="My Run") as fig:
        ax = fig.axes[0]
        assert ax.get_xlabel() == "Time (S)"
        assert ax.get_ylabel() == ylabel
        assert ax.get_title() == "My Run"


def test_build_waveform_figure_with_no_traces_does_not_crash():
    fig = build_waveform_figure([])
    try:
        assert len(fig.axes[0].lines) == 0
    finally:
        import matplotlib.pyplot as plt

        plt.close(fig)


def test_save_waveform_figure_writes_a_real_png_and_closes_the_figure(tmp_path):
    import matplotlib.pyplot as plt

    traces = [_trace("C1", [0.0, 1.0, 2.0], [1.0, 2.0, 1.0])]
    open_before = len(plt.get_fignums())

    out = save_waveform_figure(traces, tmp_path / "plot.png", title="Test")
    save_waveform_figure(traces, tmp_path / "plot2.png")

    assert out == tmp_path / "plot.png"
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # a real matplotlib-rendered image, not a stub
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
