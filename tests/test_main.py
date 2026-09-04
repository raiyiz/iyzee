"""main.py is the application entry point and hardware resource boundary."""

import iyzee.main as main_module
from iyzee.experiment.step import StepResult


class FakeMXA:
    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False


def test_main_owns_mxa_lifecycle_and_runs_bandwidth_sweep(monkeypatch, tmp_path):
    results = [
        StepResult(
            label="rbw=1Hz",
            x_value=1.0,
            x_unit="Hz",
            traces={"squeezing": [1], "shot_noise": [2]},
        )
    ]
    calls = []
    mx = FakeMXA()

    monkeypatch.setattr(main_module, "KeysightMXA", lambda: mx)
    monkeypatch.setattr(main_module, "run_bandwidth_sweep", lambda analyzer: results)
    monkeypatch.setattr(main_module, "multiplot", lambda r: calls.append(("plot", r)))
    monkeypatch.setattr(main_module, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(
        main_module,
        "save_step_results",
        lambda r, savedir: calls.append(("save", r, savedir)),
    )

    returned = main_module.main()

    assert returned == results
    assert mx.entered
    assert mx.exited
    assert calls == [("plot", results), ("save", results, tmp_path)]
