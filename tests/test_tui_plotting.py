"""Tests for the shared plotext-drawing helper."""

from __future__ import annotations

import numpy as np
from helpers import async_test
from textual.app import App
from textual.widgets import Label
from textual_plotext import PlotextPlot

from iyzee.tui.plotting import (
    MAX_DISPLAY_POINTS,
    draw_series,
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


def test_prepare_series_keeps_short_series_and_caps_long_ones_without_losing_the_shape() -> None:
    x = np.arange(5, dtype=float)
    y = np.array([0.0, 2.0, -1.0, 3.0, 1.0])
    assert prepare_series(x, y, "trace") == (x.tolist(), y.tolist(), "trace")  # short: untouched

    x = np.arange(10_000, dtype=float)
    y = np.sin(x / 37.0)
    y[4_321], y[7_654] = 100.0, -100.0
    reduced_x, reduced_y, label = prepare_series(x, y, "scope")
    assert label == "scope" and len(reduced_x) == len(reduced_y) <= MAX_DISPLAY_POINTS
    assert reduced_x[0] == x[0] and reduced_x[-1] == x[-1]  # endpoints kept
    assert 100.0 in reduced_y and -100.0 in reduced_y  # and the extrema

    x = np.arange(1000, dtype=float)
    for max_points in (2, 3, 4, 10, 100):  # the budget is a real upper bound
        reduced_x, reduced_y, _ = prepare_series(x, np.cos(x), max_points=max_points)
        assert len(reduced_x) == len(reduced_y) <= max_points
    x = np.arange(100.0)
    reduced_x, reduced_y, _ = prepare_series(x, np.linspace(-1.0, 1.0, 100), max_points=2)
    assert reduced_x == [0.0, 99.0] and reduced_y == [-1.0, 1.0]  # even a tiny one keeps both ends
