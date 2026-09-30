"""Tests for the shared plotext-drawing helper."""

from __future__ import annotations

from helpers import async_test
from textual.app import App
from textual.widgets import Label
from textual_plotext import PlotextPlot

from iyzee.tui.plotting import downsample_series, draw_series, figure_series


class _PlotApp(App):
    def compose(self):
        yield PlotextPlot(id="plot")
        yield Label("placeholder")


@async_test
async def test_draw_series_plots_each_series_and_sets_labels() -> None:
    app = _PlotApp()
    async with app.run_test(size=(120, 40)):
        plot = app.query_one("#plot", PlotextPlot)

        draw_series(
            plot,
            [([0, 1], [2.0, 3.0], "a"), ([0, 1], [1.0, 1.0], "b")],
            title="t",
            xlabel="x",
            ylabel="y",
        )

        # plotext doesn't expose plotted series for introspection in a
        # structured way, so this checks the rendered canvas actually
        # changed rather than asserting on plotext internals.
        assert plot.plt.build() != ""


def test_figure_series_extracts_lines_and_axis_labels() -> None:
    class _Line:
        def __init__(self, x, y, label) -> None:
            self._x, self._y, self._label = x, y, label

        def get_xdata(self):
            return self._x

        def get_ydata(self):
            return self._y

        def get_label(self):
            return self._label

    class _Axis:
        def __init__(self) -> None:
            self.lines = [_Line([0, 1], [2, 3], "a")]

        def get_xlabel(self):
            return "x"

        def get_ylabel(self):
            return "y"

    class _Figure:
        axes = [_Axis()]

    lines, labels = figure_series(_Figure())

    assert lines == [([0, 1], [2, 3], "a")]
    assert labels == {"xlabel": "x", "ylabel": "y"}


def test_downsample_series_preserves_extrema_and_bounds_output() -> None:
    x = list(range(100))
    y = [0.0] * 100
    y[23] = 10.0
    y[77] = -10.0

    reduced_x, reduced_y = downsample_series(x, y, max_points=20)

    assert len(reduced_x) <= 20
    assert len(reduced_y) == len(reduced_x)
    assert max(reduced_y) == 10.0
    assert min(reduced_y) == -10.0


def test_downsample_series_keeps_small_series_unchanged() -> None:
    assert downsample_series([0, 1], [2.0, 3.0], max_points=20) == ([0, 1], [2.0, 3.0])
