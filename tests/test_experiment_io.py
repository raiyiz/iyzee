import json
from pathlib import Path

import numpy as np

from iyzee.experiment.core import StepResult
from iyzee.experiment.io import (
    create_dirs,
    difference_series,
    multiplot,
    save_data,
    save_step_results,
)


def test_create_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("iyzee.experiment.io.Path", lambda *parts: Path(tmp_path, *parts))

    first = create_dirs("measurement")
    second = create_dirs("measurement")

    assert first == second
    assert first.is_dir()


def test_save_data_round_trip(tmp_path):
    data = [(1.0, np.array([1.0, 2.0]), np.array([0.5, 1.5]))]

    path = save_data(data, tmp_path)

    assert path.exists()
    assert path.suffix == ".npz"
    with np.load(path, allow_pickle=True) as archive:
        saved = archive["data"]
        assert "metadata" not in archive

    assert saved.shape == (1, 3)
    assert saved[0, 0] == 1.0
    np.testing.assert_array_equal(saved[0, 1], data[0][1])
    np.testing.assert_array_equal(saved[0, 2], data[0][2])


def test_save_data_with_metadata(tmp_path):
    data = [(1.0, [1.0], [2.0])]
    metadata = [{"rbw_hz": 1000}]

    path = save_data(data, tmp_path, metadata=metadata)

    with np.load(path, allow_pickle=True) as archive:
        assert archive["metadata"][0] == metadata[0]


def test_save_step_results_carries_per_point_and_run_metadata(tmp_path):
    results = [
        StepResult(
            label="rbw=1000Hz",
            x_value=1000.0,
            x_unit="Hz",
            traces={"squeezing": [1.0], "shot_noise": [2.0]},
            meta={"rbw_hz": 1000.0},
        )
    ]

    path = save_step_results(results, tmp_path, run_metadata={"software_revision": "abc123"})

    with np.load(path, allow_pickle=True) as archive:
        data = archive["data"]
        meta = archive["metadata"]
        run_meta = json.loads(str(archive["run_metadata"]))

    assert data[0, 0] == 1000.0
    assert meta[0]["label"] == "rbw=1000Hz"
    assert meta[0]["rbw_hz"] == 1000.0
    assert run_meta == {"software_revision": "abc123"}


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


def test_difference_series_returns_none_for_missing_traces():
    assert difference_series(None, [1.0], "pt0") is None
    assert difference_series([1.0], None, "pt0") is None
