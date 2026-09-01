from pathlib import Path

import numpy as np
import pytest

import iyzee.main as main_module
from iyzee.main import acquire_trace, create_dirs, multiplot, save_data


def test_save_data_round_trip(tmp_path):
    data = [(1.0, np.array([1.0, 2.0]), np.array([0.5, 1.5]))]

    path = save_data(data, tmp_path)

    assert path.exists()
    assert path.suffix == ".npz"
    with np.load(path, allow_pickle=True) as archive:
        saved = archive["data"]

    assert saved.shape == (1, 3)
    assert saved[0, 0] == 1.0
    np.testing.assert_array_equal(saved[0, 1], data[0][1])
    np.testing.assert_array_equal(saved[0, 2], data[0][2])


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


def test_acquire_trace_disables_trace_after_failure():
    class FakeMXA:
        def __init__(self):
            self.update_states = []

        def set_trace_update(self, trace_num, state):
            self.update_states.append((trace_num, state))

        def single_sweep_wait(self):
            raise RuntimeError("sweep failed")

    mx = FakeMXA()

    with pytest.raises(RuntimeError, match="sweep failed"):
        acquire_trace(mx, 1)

    assert mx.update_states == [(1, True), (1, False)]


def test_record_bw_seq_tracks_vbw_with_rbw(monkeypatch):
    class FakeMXA:
        def __init__(self):
            self.rbw_values = []
            self.vbw_values = []
            self.disconnect_called = False

        def set_rbw(self, rbw_hz):
            self.rbw_values.append(rbw_hz)

        def set_vbw(self, vbw_hz, auto=False):
            self.vbw_values.append((vbw_hz, auto))

        def disconnect(self):
            self.disconnect_called = True

    mx = FakeMXA()
    monkeypatch.setattr(main_module, "prepare_analyzer", lambda *args, **kwargs: mx)
    monkeypatch.setattr(
        main_module,
        "acquire_trace",
        lambda analyzer, trace_num: [trace_num],
    )

    data = main_module.record_bw_seq()

    expected_rbw = [20e3 * i for i in range(1, 20)]
    assert mx.rbw_values == expected_rbw
    assert mx.vbw_values == [(rbw * 2, False) for rbw in expected_rbw]
    assert [row[0] for row in data] == expected_rbw
    assert all(row[1:] == ([1], [2]) for row in data)
    assert mx.disconnect_called
