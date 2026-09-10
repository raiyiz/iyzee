import json
from pathlib import Path

import numpy as np

from iyzee.experiment.persistence import create_dirs, save_data, save_step_results
from iyzee.experiment.step import StepResult


def test_create_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("iyzee.experiment.persistence.Path", lambda *parts: Path(tmp_path, *parts))

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
