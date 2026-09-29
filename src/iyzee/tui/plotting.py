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
from dataclasses import dataclass
from typing import Any

from textual_plotext import PlotextPlot


@dataclass(frozen=True)
class PlotSeries:
    """Thread-safe line data prepared for a Textual plot."""

    x: tuple[float, ...]
    y: tuple[float, ...]
    label: str | None


@dataclass(frozen=True)
class PlotSnapshot:
    """All data needed to update a plot, without touching Textual widgets."""

    series: tuple[PlotSeries, ...]
    title: str | None = None
    xlabel: str | None = None
    ylabel: str | None = None


def make_plot_snapshot(
    series: Sequence[tuple[Sequence[float], Sequence[float], str | None]],
    *,
    title: str | None = None,
    xlabel: str | None = None,
    ylabel: str | None = None,
) -> PlotSnapshot:
    """Materialize plot data while still off the UI thread."""

    return PlotSnapshot(
        series=tuple(
            PlotSeries(tuple(float(value) for value in x), tuple(float(value) for value in y), label)
            for x, y, label in series
        ),
        title=title,
        xlabel=xlabel,
        ylabel=ylabel,
    )


def draw_snapshot(plot: PlotextPlot, snapshot: PlotSnapshot, *, clear: bool = True) -> None:
    """Apply a prepared snapshot to a Textual plot on the UI thread."""

    draw_series(
        plot,
        [(item.x, item.y, item.label) for item in snapshot.series],
        title=snapshot.title,
        xlabel=snapshot.xlabel,
        ylabel=snapshot.ylabel,
        clear=clear,
    )


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


def figure_series(
    fig: Any,
) -> tuple[list[tuple[list[float], list[float], str | None]], dict[str, str]]:
    """Extract line data and axis labels from a matplotlib figure."""
    lines = [
        (list(line.get_xdata()), list(line.get_ydata()), line.get_label())
        for ax in fig.axes
        for line in ax.lines
    ]
    labels = {
        "xlabel": fig.axes[0].get_xlabel() if fig.axes else "",
        "ylabel": fig.axes[0].get_ylabel() if fig.axes else "",
    }
    return lines, labels
