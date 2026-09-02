from iyzee.experiment.plotting import multiplot
from iyzee.experiment.step import StepResult


def test_multiplot_handles_empty_data(monkeypatch):
    shown = False

    def fake_show():
        nonlocal shown
        shown = True

    monkeypatch.setattr("iyzee.experiment.plotting.plt.show", fake_show)

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

    monkeypatch.setattr("iyzee.experiment.plotting.plt.subplots", lambda: (FakeFig(), FakeAx()))
    monkeypatch.setattr("iyzee.experiment.plotting.plt.show", lambda: None)

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
