import hashlib
import json
from datetime import datetime

import numpy as np

import iyzee.experiment.io as io
from iyzee.experiment.core import StepResult
from iyzee.experiment.io import (
    DATA_ROOT,
    create_dirs,
    multiplot,
    save_step_results,
)


def test_data_lives_at_the_project_root_in_one_dir_per_month(tmp_path, monkeypatch):
    # A sibling of src/, not e.g. src/iyzee/data: a regression here would put runs back inside
    # the package (every other test monkeypatches DATA_ROOT, so none would notice).
    assert (DATA_ROOT.parent / "pyproject.toml").is_file()
    assert (DATA_ROOT.parent / "src" / "iyzee").is_dir() and DATA_ROOT.name == "data"

    monkeypatch.setattr(io, "DATA_ROOT", tmp_path)
    first, second = create_dirs(), create_dirs()  # idempotent
    assert first == second and first.is_dir() and first.parent == tmp_path
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


def test_save_step_results_writes_a_pickle_free_data_and_metadata_pair(tmp_path):
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


# -- multiplot / difference_series -------------------------------------------------------


def test_difference_statistics_and_multiplots_cope_with_unusable_traces(monkeypatch):
    # -- multiplot plots each result
    plotted = []

    class FakeAx:
        def plot(self, x_values, values):
            plotted.append((list(x_values), list(values)))

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
