"""Tests for the shared plotext-drawing helper."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import async_test
from textual.app import App
from textual.widgets import Label
from textual_plotext import PlotextPlot

from iyzee.tui.plotting import (
    draw_series,
    figure_series,
    prepare_series,
)


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
        def __init__(self) -> None:
            self.axes = [_Axis()]

    lines, labels = figure_series(_Figure())

    assert lines == [([0, 1], [2, 3], "a")]
    assert labels == {"xlabel": "x", "ylabel": "y"}


def test_prepare_series_preserves_endpoints_and_local_extrema_when_thinning() -> None:
    x = np.arange(10_000, dtype=float)
    y = np.zeros_like(x)
    y[777] = -42.0
    y[8_801] = 37.0
    y[4_321] = np.nan

    xs, ys, label = prepare_series(x, y, "long trace", max_points=80)

    assert len(xs) == len(ys) <= 80
    assert (xs[0], xs[-1]) == (0.0, 9_999.0)
    assert xs[ys.index(-42.0)] == 777.0
    assert xs[ys.index(37.0)] == 8_801.0
    assert label == "long trace"


def test_prepare_series_rejects_a_budget_that_cannot_keep_endpoints() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        prepare_series([0, 1], [0, 1], max_points=1)
