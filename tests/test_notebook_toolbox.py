"""Tests for the optional notebook analysis toolbox.

Run with:
    uv run --group notebooks pytest tests/test_notebook_toolbox.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pl = pytest.importorskip("polars")

NOTEBOOKS = Path(__file__).parents[1] / "notebooks"
sys.path.insert(0, str(NOTEBOOKS))

from toolbox import (  # noqa: E402
    array_frame,
    best_points,
    csv_bytes,
    delta_to_reference,
    field_chart,
    scan_catalog,
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


def test_array_frame_selects_a_row_and_rejects_ambiguous_shapes():
    frame = array_frame(np.array([[1.0, 2.0], [3.0, 4.0]]), index=1)
    assert frame["sample"].to_list() == [0, 1]
    assert frame["value"].to_list() == [3.0, 4.0]

    with pytest.raises(ValueError, match="1-D or 2-D"):
        array_frame(np.zeros((2, 3, 4)))


def test_catalog_uses_sidecars_and_marks_synthetic_data(tmp_path):
    _save_sweep(tmp_path, "frequency-a", [377.1, 377.10001], [-2.0, -1.0], kind="frequency")
    _save_sweep(tmp_path, "rbw-baseline", [20e3, 40e3], [-2.0, -1.0])

    catalog = scan_catalog(tmp_path)

    assert catalog.height == 2
    assert set(catalog["kind"]) == {"frequency", "bandwidth"}
    assert catalog["synthetic"].to_list() == [True, True]
    assert catalog["status"].to_list() == ["completed", "completed"]
    assert all(catalog["label"].str.contains("Oct 18").to_list())
    assert set(catalog["name"]) == {"frequency-a", "rbw-baseline"}


def test_frequency_offsets_share_one_reference_between_runs(tmp_path):
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


def test_best_points_and_reference_differences_do_not_extrapolate(tmp_path):
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
    assert "delta_db" in delta.columns


def test_plot_builder_accepts_tidy_tables_and_exports_csv(tmp_path):
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


def test_verification_reports_digest_mismatch(tmp_path):
    _save_sweep(tmp_path, "rbw-a", [20e3], [-2.0])
    catalog = scan_catalog(tmp_path)

    assert verify(catalog)["integrity"].to_list() == ["matches"]
    path = Path(catalog[0, "path"])
    path.write_bytes(path.read_bytes() + b"modified")
    assert verify(catalog)["integrity"].to_list() == ["MISMATCH"]
