"""main.py is now a thin entry point; test that it wires the pieces together."""

import iyzee.main as main_module
from iyzee.experiment.step import StepResult


def test_main_runs_bandwidth_sweep_plots_and_saves(monkeypatch, tmp_path):
    results = [
        StepResult(
            label="rbw=1Hz",
            x_value=1.0,
            x_unit="Hz",
            traces={"squeezing": [1], "shot_noise": [2]},
        )
    ]
    calls = []

    monkeypatch.setattr(main_module, "run_bandwidth_sweep", lambda: results)
    monkeypatch.setattr(main_module, "multiplot", lambda r: calls.append(("plot", r)))
    monkeypatch.setattr(main_module, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(
        main_module,
        "save_step_results",
        lambda r, savedir: calls.append(("save", r, savedir)),
    )

    returned = main_module.main()

    assert returned == results
    assert calls == [("plot", results), ("save", results, tmp_path)]
