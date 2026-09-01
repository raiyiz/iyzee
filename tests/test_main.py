from pathlib import Path

import numpy as np
from iyzee.main import create_dirs, multiplot, save_data


def test_save_data_round_trip(tmp_path):
    data = [(1.0, np.array([1.0, 2.0]), np.array([0.5, 1.5]))]

    path = save_data(data, tmp_path)

    assert path.exists()
    assert path.suffix == ".npz"
    with np.load(path, allow_pickle=True) as archive:
        np.testing.assert_equal(archive["data"], np.asarray(data, dtype=object))


def test_create_dirs_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("iyzee.main.Path", lambda *parts: Path(tmp_path, *parts))

    first = create_dirs("measurement")
    second = create_dirs("measurement")

    assert first == second
    assert first.is_dir()


def test_multiplot_handles_empty_data(monkeypatch):
    shown = False

    def fake_show():
        nonlocal shown
        shown = True

    monkeypatch.setattr("iyzee.main.plt.show", fake_show)

    multiplot([])

    assert shown
