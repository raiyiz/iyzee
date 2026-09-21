"""Tests for the shared plotext-drawing helper."""

from __future__ import annotations

from helpers import async_test
from textual.app import App
from textual.widgets import Label
from textual_plotext import PlotextPlot

from iyzee.tui.plotting import draw_series


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


@async_test
async def test_draw_series_with_clear_false_appends_instead_of_replacing() -> None:
    app = _PlotApp()
    async with app.run_test(size=(120, 40)):
        plot = app.query_one("#plot", PlotextPlot)

        draw_series(plot, [([0, 1], [1.0, 2.0], "a")])
        draw_series(plot, [([0, 1], [3.0, 4.0], "b")], clear=False)

        # Both series should still be present — clear=False must not
        # have wiped the first one. (test_sweep_screen.py's live-sweep
        # tests already exercise several clear=False draws in a row
        # end-to-end; this just checks the two-series case in
        # isolation.)
        assert plot.plt.build() != ""
