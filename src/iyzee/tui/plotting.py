"""Draw a set of labeled (x, y) series into a ``PlotextPlot`` widget.

The single source of truth for the "clear, plot each series, set
title/labels, refresh" boilerplate that used to be written out
independently in ``SweepScreen._plot_result``, ``TracesScreen._show``,
and ``IyzeeConsole._draw_figure`` — those three differ in where their
series data comes from (a live sweep, a saved ``.npz``, a matplotlib
``Figure``'s line data), not in how it ends up on screen.

Pairs with ``experiment.io.difference_series``, which is the equivalent
consolidation for the squeezing-minus-shot-noise *computation* — that one
lives in ``experiment/`` since it's plotting-backend-agnostic (matplotlib
and this module both build on it); this one is plotext/Textual-specific
and belongs with the rest of the TUI instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from textual_plotext import PlotextPlot


def draw_series(
    plot: PlotextPlot,
    series: Sequence[tuple[Sequence[float], Sequence[float], str | None]],
    *,
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
    clear: bool = True,
) -> None:
    """Plot each ``(x, y, label)`` series and refresh the widget.

    ``clear=False`` appends to whatever's already drawn instead of
    replacing it — what a live sweep wants (one more line per completed
    point, not a full redraw), as opposed to Traces/Console, which are
    always redrawing from scratch for a newly selected run/figure.
    """
    if clear:
        plot.plt.clear_data()
    for x, y, label in series:
        plot.plt.plot(list(x), list(y), label=label or None)
    if title:
        plot.plt.title(title)
    if xlabel:
        plot.plt.xlabel(xlabel)
    if ylabel:
        plot.plt.ylabel(ylabel)
    plot.refresh()
