import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

import iyzee.experiment.io as io
from iyzee.experiment.core import StepResult
from iyzee.experiment.io import (
    DATA_ROOT,
    STEM_PATTERN,
    create_dirs,
    difference_series,
    multiplot,
    save_step_results,
)


def test_data_root_is_the_project_root_not_inside_the_package():
    # A sibling of src/, not e.g. src/iyzee/data — this is the behavior the
    # "move data/ to the project root" request actually depends on; a
    # regression here would put runs back inside the package without any of
    # the other tests (which monkeypatch DATA_ROOT, precisely to avoid ever
    # touching this real path) noticing.
    assert (DATA_ROOT.parent / "pyproject.toml").is_file()
    assert (DATA_ROOT.parent / "src" / "iyzee").is_dir()
    assert DATA_ROOT.name == "data"


def test_create_dirs_is_idempotent_and_groups_by_month(tmp_path, monkeypatch):
    monkeypatch.setattr(io, "DATA_ROOT", tmp_path)

    first = create_dirs()
    second = create_dirs()

    assert first == second
    assert first.is_dir()
    assert first.parent == tmp_path
    assert first.name == datetime.now().astimezone().strftime("%Y-%m")


# -- save_step_results: the numeric .npz + JSON sidecar pair -----------------------------


def _result(x_value=1.0, *, squeezing=None, shot_noise=None, **meta) -> StepResult:
    traces = {}
    if squeezing is not None:
        traces["squeezing"] = squeezing
    if shot_noise is not None:
        traces["shot_noise"] = shot_noise
    return StepResult(
        label=f"x={x_value}", x_value=x_value, x_unit="Hz", traces=traces, meta=dict(meta)
    )


def test_save_step_results_writes_data_and_metadata_pair(tmp_path):
    results = [
        _result(
            1.0,
            squeezing=[3.0, 4.0],
            shot_noise=[1.0, 1.0],
            rbw_hz=1000.0,
        )
    ]
    path = save_step_results(results, tmp_path, run_metadata={"software_revision": "abc123"})

    assert path.suffix == ".npz"
    json_path = path.with_suffix(".json")
    assert json_path.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [json_path.name, path.name], (
        "no stray .part file"
    )

    # The numeric archive is deliberately pickle-free; metadata lives in the JSON
    # sidecar so loading sweep data never needs unsafe pickle deserialization.
    with np.load(path, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["x_values"], [1.0])
        np.testing.assert_array_equal(archive["trace_squeezing"], [[3.0, 4.0]])
        np.testing.assert_array_equal(archive["trace_shot_noise"], [[1.0, 1.0]])

    sidecar = json.loads(json_path.read_text())
    assert sidecar["points"] == [{"label": "x=1.0", "x_unit": "Hz", "rbw_hz": 1000.0}]
    assert sidecar["run_metadata"] == {"software_revision": "abc123"}
    # Per-point metadata and run-level metadata stay in the sidecar rather than
    # contaminating the numeric NPZ payload.


def test_save_step_results_can_overwrite_a_fixed_file_pair_atomically(tmp_path: Path) -> None:
    target = tmp_path / "checkpoint.npz"
    assert save_step_results([_result(1.0)], tmp_path, path=target) == target
    again = save_step_results([_result(1.0), _result(2.0)], tmp_path, path=target)

    assert again == target
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "checkpoint.json",
        "checkpoint.npz",
    ], "no stray .part file, and the sidecar follows the .npz's chosen name"
    with np.load(target, allow_pickle=False) as archive:
        assert len(archive["x_values"]) == 2


def test_save_step_results_fills_a_missing_trace_with_nan(tmp_path):
    results = [
        _result(1.0, squeezing=[1.0, 2.0], shot_noise=[3.0, 4.0]),
        _result(2.0, squeezing=[5.0, 6.0]),  # no shot_noise for this point
    ]

    path = save_step_results(results, tmp_path)

    with np.load(path, allow_pickle=False) as archive:
        shot_noise = archive["trace_shot_noise"]
    np.testing.assert_array_equal(shot_noise[0], [3.0, 4.0])
    assert np.all(np.isnan(shot_noise[1]))


def test_save_step_results_rejects_a_trace_whose_length_disagrees_across_points(tmp_path):
    results = [
        _result(1.0, squeezing=[1.0, 2.0, 3.0]),
        _result(2.0, squeezing=[1.0, 2.0]),
    ]

    with pytest.raises(ValueError, match="squeezing.*inconsistent lengths"):
        save_step_results(results, tmp_path)


def test_save_step_results_names_the_file_from_name_and_a_random_suffix(tmp_path):
    path = save_step_results([_result(1.0)], tmp_path, name="bandwidth")

    match = STEM_PATTERN.match(path.stem)
    assert match is not None
    assert match["name"] == "bandwidth"


def test_save_step_results_sanitizes_an_unsafe_name(tmp_path):
    path = save_step_results([_result(1.0)], tmp_path, name="bandwidth sweep/#1")

    match = STEM_PATTERN.match(path.stem)
    assert match is not None
    assert match["name"] == "bandwidth-sweep-1"


def test_two_saves_in_the_same_second_do_not_collide(tmp_path):
    # No time-freezing needed: two calls back-to-back in a test almost
    # certainly land in the same wall-clock second anyway, so this already
    # exercises the random suffix's actual job.
    first = save_step_results([_result(1.0)], tmp_path, name="bandwidth")
    second = save_step_results([_result(1.0)], tmp_path, name="bandwidth")

    assert first != second


# -- multiplot / difference_series -------------------------------------------------------


def test_multiplot_handles_empty_data(monkeypatch):
    shown = False

    def fake_show():
        nonlocal shown
        shown = True

    monkeypatch.setattr("iyzee.experiment.io.plt.show", fake_show)

    multiplot([])

    assert shown


def test_multiplot_plots_each_result(monkeypatch):
    plotted = []

    class FakeAx:
        def plot(self, values):
            plotted.append(list(values))

        def legend(self, *a, **k):
            pass

        def set_xlabel(self, *a, **k):
            pass

        def set_ylabel(self, *a, **k):
            pass

    class FakeFig:
        def tight_layout(self):
            pass

    monkeypatch.setattr("iyzee.experiment.io.plt.subplots", lambda: (FakeFig(), FakeAx()))
    monkeypatch.setattr("iyzee.experiment.io.plt.show", lambda: None)

    results = [
        StepResult(
            label="x=1",
            x_value=1.0,
            x_unit="Hz",
            traces={"squeezing": [3.0, 4.0], "shot_noise": [1.0, 1.0]},
        )
    ]

    multiplot(results)

    assert plotted == [[2.0, 3.0]]


def test_difference_series_computes_x_y_and_label():
    result = difference_series([3.0, 4.0], [1.0, 1.0], "pt0")

    assert result == ([0, 1], [2.0, 3.0], "pt0")


def test_difference_series_returns_none_without_usable_traces():
    # A missing trace and a trace that is only NaNs are both unusable.
    assert difference_series(None, [1.0], "pt0") is None
    assert difference_series([1.0], None, "pt0") is None
    assert difference_series([np.nan, np.nan], [1.0, 1.0], "pt0") is None
