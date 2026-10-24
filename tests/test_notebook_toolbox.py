"""Tests for the optional notebook analysis toolbox.

Run with:
    uv run --group notebooks pytest tests/test_notebook_toolbox.py
"""

from __future__ import annotations

import math
import sys
import typing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


pl = pytest.importorskip("polars")

ROOT = Path(__file__).parents[1]
NOTEBOOKS = ROOT / "notebooks"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(NOTEBOOKS))
sys.path.insert(0, str(SCRIPTS))

import toolbox  # noqa: E402
from demo_data import write_demo  # noqa: E402
from toolbox import (  # noqa: E402
    array_frame,
    best_points,
    csv_bytes,
    decimate,
    delta_to_reference,
    field_chart,
    scan_catalog,
    scope_channels,
    scope_traces,
    sweep_points,
    verify,
)

from iyzee.experiment import StepResult, save_step_results  # noqa: E402


def _save_sweep(
    root: Path,
    name: str,
    x_values: list[float],
    relative_noise: list[float],
    *,
    kind: str = "bandwidth",
) -> Path:
    folder = root / "2026-10"
    folder.mkdir(parents=True, exist_ok=True)
    unit = "THz" if kind == "frequency" else "Hz"
    results = []
    for index, (x_value, noise) in enumerate(zip(x_values, relative_noise, strict=True)):
        meta = (
            {
                "wavemeter_channel": 4,
                "measured_frequency_thz": x_value,
            }
            if kind == "frequency"
            else {"rbw_hz": x_value}
        )
        results.append(
            StepResult(
                label=f"point {index}",
                x_value=x_value,
                x_unit=unit,
                traces={
                    "squeezing": np.full(4, -60.0 + noise),
                    "shot_noise": np.full(4, -60.0),
                },
                meta=meta,
            )
        )
    path = folder / f"18T100000_{name}_abc123.npz"
    return save_step_results(
        results,
        folder,
        {
            "status": "completed",
            "started_at_utc": "2026-10-18T10:00:00Z",
            "synthetic": True,
        },
        path=path,
    )

def test_array_frame_selects_a_row_and_rejects_ambiguous_shapes() -> None:
    frame = array_frame(np.array([[1.0, 2.0], [3.0, 4.0]]), index=1)
    assert frame["sample"].to_list() == [0, 1]
    assert frame["value"].to_list() == [3.0, 4.0]

    with pytest.raises(ValueError, match="1-D or 2-D"):
        array_frame(np.zeros((2, 3, 4)))

def test_catalog_uses_sidecars_and_marks_synthetic_data(tmp_path: typing.Any) -> None:
    _save_sweep(tmp_path, "frequency-a", [377.1, 377.10001], [-2.0, -1.0], kind="frequency")
    _save_sweep(tmp_path, "rbw-baseline", [20e3, 40e3], [-2.0, -1.0])

    catalog = scan_catalog(tmp_path)

    assert catalog.height == 2
    assert set(catalog["kind"]) == {"frequency", "bandwidth"}
    assert catalog["synthetic"].to_list() == [True, True]
    assert catalog["status"].to_list() == ["completed", "completed"]
    assert all(catalog["label"].str.contains("Oct 18").to_list())
    assert all(catalog["label"].str.contains("SYNTHETIC").to_list())
    assert set(catalog["name"]) == {"frequency-a", "rbw-baseline"}

def test_frequency_offsets_share_one_reference_between_runs(tmp_path: typing.Any) -> None:
    _save_sweep(
        tmp_path,
        "frequency-a",
        [377.100000, 377.100010],
        [-2.0, -1.0],
        kind="frequency",
    )
    _save_sweep(
        tmp_path,
        "frequency-b",
        [377.100100, 377.100110],
        [-3.0, -2.0],
        kind="frequency",
    )
    catalog = scan_catalog(tmp_path)
    points = sweep_points(catalog)

    first = points.filter(pl.col("run").str.contains("frequency-a")).sort("point")
    second = points.filter(pl.col("run").str.contains("frequency-b")).sort("point")
    assert first["offset_mhz"].to_list() == pytest.approx([-55.0, -45.0])
    assert second["offset_mhz"].to_list() == pytest.approx([45.0, 55.0])
    assert first["rel_noise_mean_db"].to_list() == pytest.approx([-2.0, -1.0])
    assert first["synthetic"].to_list() == [True, True]
    assert all(label.startswith("SYNTHETIC · ") for label in first["run"].to_list())

def test_best_points_and_reference_differences_do_not_extrapolate(tmp_path: typing.Any) -> None:
    _save_sweep(tmp_path, "rbw-a", [20e3, 40e3, 60e3], [-2.0, 1.0, -3.0])
    _save_sweep(tmp_path, "rbw-b", [20e3, 40e3, 60e3], [-1.0, 0.0, -2.0])
    catalog = scan_catalog(tmp_path)
    points = sweep_points(catalog)

    best = best_points(points)
    assert best.height == 2
    assert best[0, "rel_noise_mean_db"] == pytest.approx(-3.0)

    reference_id = catalog.filter(pl.col("name") == "rbw-a")[0, "run_id"]
    delta = delta_to_reference(points, reference_id).sort("rbw_khz")
    assert delta["delta_db"].to_list() == pytest.approx([1.0, -1.0, 1.0])
    assert delta["synthetic"].to_list() == [True, True, True]
    assert "delta_db" in delta.columns

def test_plot_builder_accepts_tidy_tables_and_exports_csv(tmp_path: typing.Any) -> None:
    _save_sweep(tmp_path, "rbw-a", [20e3, 40e3, 60e3], [-2.0, 1.0, -3.0])
    points = sweep_points(scan_catalog(tmp_path))
    figure = field_chart(
        points,
        x="rbw_khz",
        y="rel_noise_mean_db",
        color="run",
        title="A reusable plot",
    )
    assert figure.axes[0].get_title(loc="left") == "A reusable plot"
    assert b"rel_noise_mean_db" in csv_bytes(points)

def test_verification_reports_digest_mismatch(tmp_path: typing.Any) -> None:
    _save_sweep(tmp_path, "rbw-a", [20e3], [-2.0])
    catalog = scan_catalog(tmp_path)

    assert verify(catalog)["integrity"].to_list() == ["matches"]
    path = Path(catalog[0, "path"])
    path.write_bytes(path.read_bytes() + b"modified")
    assert verify(catalog)["integrity"].to_list() == ["MISMATCH"]

def test_scope_trace_decoding_is_cached_across_display_options(monkeypatch: pytest.MonkeyPatch, tmp_path: typing.Any) -> None:
    path = tmp_path / "scope.npz"
    np.savez(path, placeholder=np.arange(4))
    calls = 0

    def decode(_recording: typing.Any) -> tuple[typing.Any, ...]:
        nonlocal calls
        calls += 1
        time = np.linspace(-1e-3, 1e-3, 100)
        return (
            SimpleNamespace(
                label="C1",
                time=time,
                values=np.sin(np.linspace(0, 10, 100)),
                time_unit="S",
                value_unit="V",
            ),
            SimpleNamespace(
                label="C2",
                time=time,
                values=np.cos(np.linspace(0, 10, 100)),
                time_unit="S",
                value_unit="V",
            ),
        )

    rows = pl.DataFrame(
        {
            "kind": ["scope"],
            "path": [str(path)],
            "label": ["SYNTHETIC · scope test"],
            "run_id": ["scope-test"],
            "synthetic": [True],
        }
    )
    toolbox._cached_scope_traces.cache_clear()
    monkeypatch.setattr(toolbox, "traces_from_scope_recording", decode)
    try:
        first = toolbox.scope_traces(rows, ["C1"], budget=20)
        second = toolbox.scope_traces(rows, ["C2"], budget=40)
    finally:
        toolbox._cached_scope_traces.cache_clear()

    assert calls == 1
    assert set(first["channel"]) == {"C1"}
    assert set(second["channel"]) == {"C2"}
    assert first["synthetic"].to_list() == [True] * first.height
    assert second["synthetic"].to_list() == [True] * second.height

def test_scope_decimation_preserves_extrema_within_the_point_budget() -> None:
    time = np.arange(1000, dtype=float)
    values = np.zeros(1000)
    values[117] = 25.0
    values[703] = -22.0

    reduced_time, reduced_values = decimate(time, values, budget=100)

    assert len(reduced_values) <= 100
    assert len(reduced_time) == len(reduced_values)
    assert reduced_values.max() == 25.0
    assert reduced_values.min() == -22.0

def test_synthetic_scope_recordings_flow_through_analysis_helpers(tmp_path: typing.Any) -> None:
    paths = write_demo(
        tmp_path,
        now=datetime(2026, 10, 19, 12, tzinfo=UTC),
        seed=3,
    )
    catalog = scan_catalog(tmp_path)
    scopes = catalog.filter(pl.col("kind") == "scope")

    assert len(paths) == 11
    assert scopes.height == 3
    assert scopes["synthetic"].to_list() == [True, True, True]

    channel_stats = scope_channels(scopes)
    assert set(channel_stats["channel"]) == {"C1", "C2", "C3"}
    assert channel_stats["synthetic"].all()
    assert channel_stats.filter(pl.col("channel") == "C2")["peak_to_peak"].min() > 0

    traces = scope_traces(scopes, ["C2"], budget=200)
    assert set(traces["channel"]) == {"C2"}
    assert traces.height <= 3 * 200
    assert set(traces["time_unit"]) == {"ms"}
    assert traces["synthetic"].all()
    assert all(math.isfinite(value) for value in traces["value"].to_list())
