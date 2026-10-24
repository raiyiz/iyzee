import hashlib
import json
import typing
from datetime import datetime

import numpy as np
import pytest

import iyzee.experiment.io as io
from iyzee.experiment.core import StepResult
from iyzee.experiment.io import (
    DATA_ROOT,
    STEM_PATTERN,
    create_dirs,
    difference_series,
    difference_series_many,
    multiplot,
    save_step_results,
)


def test_data_lives_at_the_project_root_in_one_dir_per_month(
    tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A sibling of src/, not e.g. src/iyzee/data: a regression here would put runs back inside
    # the package (every other test monkeypatches DATA_ROOT, so none would notice).
    assert (DATA_ROOT.parent / "pyproject.toml").is_file()
    assert (DATA_ROOT.parent / "src" / "iyzee").is_dir() and DATA_ROOT.name == "data"

    monkeypatch.setattr(io, "DATA_ROOT", tmp_path)
    first, second = create_dirs(), create_dirs()  # idempotent
    assert first == second and first.is_dir() and first.parent == tmp_path
    assert first.name == datetime.now().astimezone().strftime("%Y-%m")


# -- save_step_results: the numeric .npz + JSON sidecar pair -----------------------------


def _result(
    x_value: float = 1.0,
    *,
    squeezing: typing.Any = None,
    shot_noise: typing.Any = None,
    **meta: typing.Any,
) -> StepResult:
    traces = {}
    if squeezing is not None:
        traces["squeezing"] = squeezing
    if shot_noise is not None:
        traces["shot_noise"] = shot_noise
    return StepResult(
        label=f"x={x_value}", x_value=x_value, x_unit="Hz", traces=traces, meta=dict(meta)
    )


def test_save_step_results_writes_a_pickle_free_data_and_metadata_pair(tmp_path: typing.Any) -> None:
    results = [_result(1.0, squeezing=[3.0, 4.0], shot_noise=[1.0, 1.0], rbw_hz=1000.0)]
    path = save_step_results(results, tmp_path, run_metadata={"software_revision": "abc123"})

    assert path.suffix == ".npz"
    json_path = path.with_suffix(".json")
    assert sorted(p.name for p in tmp_path.iterdir()) == [json_path.name, path.name], (
        "no stray .part file"
    )
    # Pickle-free: metadata lives in the JSON sidecar so loading never needs unsafe unpickling.
    with np.load(path, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive["x_values"], [1.0])
        np.testing.assert_array_equal(archive["trace_squeezing"], [[3.0, 4.0]])
        np.testing.assert_array_equal(archive["trace_shot_noise"], [[1.0, 1.0]])

    sidecar = json.loads(json_path.read_text())
    assert sidecar["format"] == "iyzee.numeric-recording"
    assert sidecar["data_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sidecar["points"] == [{"label": "x=1.0", "x_unit": "Hz", "rbw_hz": 1000.0}]
    assert sidecar["run_metadata"] == {"software_revision": "abc123"}


def test_save_step_results_aligns_traces_across_points(tmp_path: typing.Any) -> None:
    missing = [
        _result(1.0, squeezing=[1.0, 2.0], shot_noise=[3.0, 4.0]),
        _result(2.0, squeezing=[5.0, 6.0]),  # no shot_noise for this point
    ]
    with np.load(save_step_results(missing, tmp_path), allow_pickle=False) as archive:
        shot_noise = archive["trace_shot_noise"]
    np.testing.assert_array_equal(shot_noise[0], [3.0, 4.0])
    assert np.all(np.isnan(shot_noise[1]))  # filled, not dropped

    ragged = [_result(1.0, squeezing=[1.0, 2.0, 3.0]), _result(2.0, squeezing=[1.0, 2.0])]
    with pytest.raises(ValueError, match=r"squeezing.*inconsistent lengths"):
        save_step_results(ragged, tmp_path)


def test_save_step_results_names_files_safely_uniquely_or_exactly_as_asked(tmp_path: typing.Any) -> None:
    first = save_step_results([_result(1.0)], tmp_path, name="bandwidth sweep/#1")
    second = save_step_results([_result(1.0)], tmp_path, name="bandwidth sweep/#1")
    match = STEM_PATTERN.match(first.stem)
    assert match is not None and match["name"] == "bandwidth-sweep-1"  # sanitized
    assert first != second  # same second, same name: the random suffix keeps them apart

    target = tmp_path / "checkpoint.npz"  # or a fixed pair, overwritten atomically
    assert save_step_results([_result(1.0)], tmp_path, path=target) == target
    assert save_step_results([_result(1.0), _result(2.0)], tmp_path, path=target) == target
    assert (tmp_path / "checkpoint.json").exists() and not list(tmp_path.glob("*.part"))
    with np.load(target, allow_pickle=False) as archive:
        assert len(archive["x_values"]) == 2


# -- multiplot / difference_series -------------------------------------------------------


def test_difference_statistics_and_series_handle_unusable_traces() -> None:
    assert io.difference_statistic([3.0, 4.0], [1.0, 1.0], "mean") == pytest.approx(2.5)
    assert io.difference_statistic([3.0, 4.0], [1.0, 1.0], "minimum") == pytest.approx(2.0)
    assert io.difference_statistic([np.nan, 4.0], [1.0, 1.0], "mean") == pytest.approx(3.0)
    assert io.difference_statistic([np.nan], [1.0], "mean") is None
    assert io.difference_values_many(
        [[3.0, 4.0], [5.0, 6.0]], [[1.0, 1.0], [2.0, 2.0]], "minimum"
    ) == [2.0, 3.0]
    with pytest.raises(ValueError, match="unknown statistic"):
        io.difference_statistic([1.0], [0.0], "median")

    assert difference_series([3.0, 4.0], [1.0, 1.0], "pt0") == ([0, 1], [2.0, 3.0], "pt0")
    # A missing trace and a trace that is only NaNs are both unusable.
    assert difference_series(None, [1.0], "pt0") is None
    assert difference_series([1.0], None, "pt0") is None
    assert difference_series([np.nan, np.nan], [1.0, 1.0], "pt0") is None
    assert difference_series_many(
        [[3.0, 4.0], None, [5.0, 6.0]],
        [[1.0, 1.0], [1.0, 1.0], [2.0, 2.0]],
        ["first", "missing", "third"],
    ) == [([0, 1], [2.0, 3.0], "first"), ([0, 1], [3.0, 4.0], "third")]


def test_multiplot_plots_each_result(monkeypatch: pytest.MonkeyPatch) -> None:
    plotted = []

    class FakeAx:
        def plot(self, x_values: typing.Any, values: typing.Any) -> None:
            plotted.append((list(x_values), list(values)))

        def legend(self, *a: typing.Any, **k: typing.Any) -> None:
            pass

        def set_xlabel(self, *a: typing.Any, **k: typing.Any) -> None:
            pass

        def set_ylabel(self, *a: typing.Any, **k: typing.Any) -> None:
            pass

    class FakeFig:
        def tight_layout(self) -> None:
            pass

    monkeypatch.setattr("iyzee.experiment.io.plt.subplots", lambda: (FakeFig(), FakeAx()))
    monkeypatch.setattr("iyzee.experiment.io.plt.show", lambda: None)

    multiplot(
        [
            StepResult(
                label="x=1",
                x_value=1.0,
                x_unit="Hz",
                traces={"squeezing": [3.0, 4.0], "shot_noise": [1.0, 1.0]},
            )
        ]
    )

    assert plotted == [([0, 1], [2.0, 3.0])]
