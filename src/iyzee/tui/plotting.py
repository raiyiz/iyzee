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

MAX_DISPLAY_POINTS = 2000


def prepare_series(
    x: Sequence[float],
    y: Sequence[float],
    label: str | None = None,
    *,
    max_points: int = MAX_DISPLAY_POINTS,
) -> tuple[list[float], list[float], str | None]:
    """Reduce one series to a terminal-sized representation.

    The saved recording is never changed: this only prepares data for a
    Plotext preview. Each bucket keeps its local minimum and maximum so
    narrow waveform features survive the reduction better than uniform
    slicing does. Non-finite samples are ignored while choosing extrema.
    """
    if max_points < 2:
        raise ValueError("max_points must be at least 2")
    length = min(len(x), len(y))
    if length == 0:
        return [], [], label

    x_values = np.asarray(x, dtype=np.float64)[:length]
    y_values = np.asarray(y, dtype=np.float64)[:length]
    if length <= max_points:
        return x_values.tolist(), y_values.tolist(), label
    if max_points == 2:
        indices = [0, length - 1]
        return x_values[indices].tolist(), y_values[indices].tolist(), label
    if max_points == 3:
        interior = y_values[1:-1]
        if len(interior) == 0:
            indices = [0, length - 1]
        else:
            finite = np.isfinite(interior)
            candidates = np.flatnonzero(finite)
            if len(candidates):
                local = candidates[np.argmax(np.abs(interior[finite]))]
            else:
                local = 0
            indices = [0, int(local) + 1, length - 1]
        return x_values[indices].tolist(), y_values[indices].tolist(), label

    bucket_count = max(1, (max_points - 2) // 2)
    edges = np.linspace(1, length - 1, bucket_count + 1, dtype=np.intp)
    indices = [0]
    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        bucket = y_values[start:end]
        finite = np.isfinite(bucket)
        if not np.any(finite):
            continue
        finite_indices = np.flatnonzero(finite)
        local_min = int(finite_indices[np.argmin(bucket[finite])]) + int(start)
        local_max = int(finite_indices[np.argmax(bucket[finite])]) + int(start)
        indices.extend(sorted({local_min, local_max}))
    indices.append(length - 1)

    indices = list(dict.fromkeys(indices))
    return x_values[indices].tolist(), y_values[indices].tolist(), label


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
