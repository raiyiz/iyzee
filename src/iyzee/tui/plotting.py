"""Draw a set of labeled (x, y) series into a ``PlotextPlot`` widget.

The single source of truth for the "clear, plot each series, set
title/labels, refresh" boilerplate that used to be written out
independently in ``SweepScreen._plot_result``, ``ResultsScreen``,
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

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from textual_plotext import PlotextPlot

MAX_DISPLAY_POINTS = 2000

Samples = Sequence[float] | np.ndarray


def prepare_series(
    x: Samples,
    y: Samples,
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
    point, not a full redraw), as opposed to Results/Console, which are
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


_SI_PREFIXES = (
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
    (1e-9, "n"),
    (1e-12, "p"),
    (1e-15, "f"),
)


def format_si(value: float, unit: str) -> str:
    """``2.5e-6, "s"`` -> ``"2.5 µs"``; values outside the prefix range fall back to ``%g``."""
    if value == 0 or not math.isfinite(value):
        return f"{value:g} {unit}"
    for scale, prefix in _SI_PREFIXES:
        if abs(value) >= scale * (1 - 1e-9):
            return f"{value / scale:.4g} {prefix}{unit}"
    return f"{value:g} {unit}"


def describe_timebase(offset: float, interval: float, samples: int, unit: str = "S") -> list[str]:
    """Two readable lines for a recorded trace's horizontal setup.

    ``offset`` is the time of the first sample relative to the trigger, so a
    negative offset is pre-trigger data and the trigger sits inside the window.
    """
    is_seconds = unit.strip().lower() in ("s", "sec")
    fmt = (lambda v: format_si(v, "s")) if is_seconds else (lambda v: f"{v:g} {unit}")
    end = offset + (samples - 1) * interval
    lines = [f"Window: {fmt(offset)} to {fmt(end)} ({fmt(samples * interval)}, {samples} samples)"]
    rate = f", {format_si(1 / interval, 'S/s')}" if is_seconds and interval > 0 else ""
    lines.append(f"Sample interval: {fmt(interval)}{rate}")
    return lines


def time_axis(unit: str, span: float) -> tuple[float, str]:
    """Pick a readable unit for a time axis spanning ``span``.

    Returns ``(factor to multiply the samples by, unit label)``. Anything that
    isn't plain seconds is left alone.
    """
    if unit.strip().lower() not in ("s", "sec") or not math.isfinite(span) or span <= 0:
        return 1.0, unit
    for scale, prefix in _SI_PREFIXES[4:9]:  # s, ms, µs, ns, ps
        if span >= scale:
            return 1.0 / scale, f"{prefix}s"
    return 1e12, "ps"


def time_series(
    items: Sequence[tuple[Samples, Samples, str]], unit: str
) -> tuple[list[tuple[list[float], list[float], str | None]], str]:
    """Terminal-sized ``(x, y, label)`` series on a shared, readable time axis.

    ``items`` is ``(time, values, label)`` per trace; returns the series plus
    the matching ``"Time (µs)"``-style axis label.
    """
    span = max((float(np.max(np.abs(t))) for t, _, _ in items if len(t)), default=0.0)
    factor, label = time_axis(unit, span)
    series = [prepare_series(np.asarray(t, dtype=float) * factor, v, name) for t, v, name in items]
    return series, f"Time ({label})"
