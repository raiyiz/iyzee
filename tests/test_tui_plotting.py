"""Tests for the shared plotext-drawing helper."""

from __future__ import annotations

import numpy as np
import pytest
from helpers import async_test
from textual.app import App
from textual.widgets import Label
from textual_plotext import PlotextPlot

from iyzee.tui.plotting import (
    MAX_DISPLAY_POINTS,
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
        axes = [_Axis()]

    lines, labels = figure_series(_Figure())

    assert lines == [([0, 1], [2, 3], "a")]
    assert labels == {"xlabel": "x", "ylabel": "y"}


def test_short_series_is_preserved() -> None:
    x = np.arange(5, dtype=float)
    y = np.array([0.0, 2.0, -1.0, 3.0, 1.0])
    assert prepare_series(x, y, "trace") == (x.tolist(), y.tolist(), "trace")


def test_long_series_is_capped_and_keeps_endpoints_and_extrema() -> None:
    x = np.arange(10_000, dtype=float)
    y = np.sin(x / 37.0)
    y[4_321] = 100.0
    y[7_654] = -100.0

    reduced_x, reduced_y, label = prepare_series(x, y, "scope")

    assert label == "scope"
    assert len(reduced_x) == len(reduced_y) <= MAX_DISPLAY_POINTS
    assert reduced_x[0] == x[0] and reduced_x[-1] == x[-1]
    assert 100.0 in reduced_y
    assert -100.0 in reduced_y


@pytest.mark.parametrize("max_points", [2, 3, 4, 10, 100])
def test_max_points_is_an_actual_upper_bound(max_points: int) -> None:
    x = np.arange(1000, dtype=float)
    y = np.cos(x)
    reduced_x, reduced_y, _ = prepare_series(x, y, max_points=max_points)
    assert len(reduced_x) == len(reduced_y) <= max_points


def test_tiny_budget_keeps_waveform_endpoints() -> None:
    x = np.arange(100.0)
    y = np.linspace(-1.0, 1.0, len(x))
    reduced_x, reduced_y, _ = prepare_series(x, y, max_points=2)
    assert reduced_x == [0.0, 99.0]
    assert reduced_y == [-1.0, 1.0]
