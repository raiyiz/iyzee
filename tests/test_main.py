import numpy as np
from iyzee.main import create_dirs, save_data


def test_save_data_round_trip(tmp_path):
    data = [[1.0, [1.0, 2.0], [3.0, 4.0]]]
    path = save_data(data, tmp_path)

    assert path.suffix == ".npz"
    with np.load(path, allow_pickle=True) as archive:
        saved = archive["data"]
    assert saved.tolist() == data


def test_create_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("main.Path", lambda *args: tmp_path)

    first = create_dirs("measurement")
    second = create_dirs("measurement")

    assert first == second
    assert first.is_dir()
