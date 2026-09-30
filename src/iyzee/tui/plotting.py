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
from typing import Any

import numpy as np
from textual_plotext import PlotextPlot


MAX_PLOT_POINTS = 2_000


def downsample_series(
    x: Sequence[float], y: Sequence[float], *, max_points: int = MAX_PLOT_POINTS
) -> tuple[list[float], list[float]]:
    """Reduce a dense series to a bounded number of points without losing spikes.

    Min/max pairs are retained per bucket rather than taking every Nth sample,
    so narrow peaks remain visible while terminal rendering stays bounded.
    The operation is pure numeric work and is safe to run in a Textual worker.
    """
    if max_points < 2:
        raise ValueError("max_points must be at least 2")
    x_array = np.asarray(x)
    y_array = np.asarray(y)
    if x_array.size != y_array.size:
        raise ValueError("x and y must contain the same number of samples")
    if x_array.size <= max_points:
        return x_array.tolist(), y_array.tolist()

    bucket_count = max(1, max_points // 2)
    edges = np.linspace(0, x_array.size, bucket_count + 1, dtype=np.int64)
    out_x: list[float] = []
    out_y: list[float] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        bucket = y_array[start:stop]
        low = start + int(np.argmin(bucket))
        high = start + int(np.argmax(bucket))
        if low <= high:
            indices = (low, high)
        else:
            indices = (high, low)
        for index in indices:
            out_x.append(float(x_array[index]))
            out_y.append(float(y_array[index]))
    return out_x, out_y


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
