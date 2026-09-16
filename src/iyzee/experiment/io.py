"""Where experiment results go: measurement persistence (run directories,
compressed archives) and plotting helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from .core import StepResult


def create_dirs(name: str = "") -> Path:
    """Create and return today's measurement-data directory."""
    package_root = Path(__file__).resolve().parent.parent
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    data_dir = package_root / "data" / Path(today + "_" + name)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def save_data(
    data,
    savedir: Path,
    metadata: list[dict] | None = None,
    **extra_arrays: Any,
) -> Path:
    """Save variable-length trace data in a compressed NumPy archive.

    ``metadata``, if given, is a per-point list of JSON-serializable dicts
    (same length/order as ``data``) describing the instrument state that
    produced each point. ``extra_arrays`` lets callers attach additional
    top-level arrays (e.g. run-level metadata) without another signature
    change later.
    """
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = savedir / f"{timestamp}.npz"
    arrays: dict[str, Any] = {"data": np.asarray(data, dtype=object)}
    if metadata is not None:
        arrays["metadata"] = np.asarray(metadata, dtype=object)
    arrays.update(extra_arrays)
    np.savez_compressed(path, **arrays)
    return path


def save_step_results(
    results: list[StepResult],
    savedir: Path,
    run_metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a list of :class:`StepResult` with their per-point metadata.

    This is the preferred way to persist an experiment run: unlike the raw
    ``save_data()`` tuples, the saved archive is self-describing — every
    point carries the instrument state that produced it, and the run as a
    whole can carry a software revision, analyzer config, and timestamp via
    ``run_metadata``.
    """
    data = [
        (result.x_value, result.traces.get("squeezing"), result.traces.get("shot_noise"))
        for result in results
    ]
    per_point_meta = [
        {"label": result.label, "x_unit": result.x_unit, **result.meta} for result in results
    ]
    extra: dict[str, Any] = {}
    if run_metadata is not None:
        extra["run_metadata"] = np.asarray(json.dumps(run_metadata))
    return save_data(data, savedir, metadata=per_point_meta, **extra)


def difference_series(
    squeezing: object, shot_noise: object, label: str | None
) -> tuple[list[float], list[float], str | None] | None:
    """Compute one squeezing-minus-shot-noise line, ready to plot.

    The single source of truth for this computation — before this, the
    same three lines (subtract, build an x-index, attach a label) were
    written out independently in four places: this module's own
    ``build_figure`` (matplotlib), ``SweepScreen._plot_result`` (live,
    per point, plotext), ``TracesScreen._show`` (re-derived from a saved
    ``.npz``, plotext), and indirectly duplicated again in spirit by
    ``IyzeeConsole``'s figure rendering. Callers only differ in *where*
    the squeezing/shot_noise arrays came from (a live ``StepResult`` vs.
    an archived point) and *how* they draw the result (matplotlib vs.
    plotext) — this covers the part in between.

    Returns ``None`` if either trace is missing, matching the skip
    behavior ``SweepScreen``/``TracesScreen`` already had (``build_figure``
    previously didn't guard against this and would have raised on missing
    traces — this closes that gap as a side effect of consolidating).
    """
    if squeezing is None or shot_noise is None:
        return None
    difference = np.asarray(squeezing) - np.asarray(shot_noise)
    return list(range(len(difference))), list(difference), label


def build_figure(results: list[StepResult]):
    """Build (but do not display) the squeezing-minus-shot-noise figure.

    Split out of :func:`multiplot` so non-interactive callers — saving to
    disk, or a TUI that renders traces itself with something like
    ``textual-plotext`` — can get the figure without ``matplotlib`` trying
    to pop up a blocking GUI window.
    """
    fig, ax = plt.subplots()
    labels = []

    for result in results:
        series = difference_series(
            result.traces.get("squeezing"), result.traces.get("shot_noise"), result.label
        )
        if series is None:
            continue
        _x, difference, label = series
        ax.plot(difference)
        labels.append(label)

    if labels:
        ax.legend(labels, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.1))
    ax.set_xlabel("Trace point")
    ax.set_ylabel("Squeezing - shot noise")
    fig.tight_layout()
    return fig


def multiplot(results: list[StepResult]) -> None:
    """Build and display the squeezing-minus-shot-noise figure.

    Kept for the script/CLI entry point (``main.py``) and existing callers.
    Non-interactive callers should use :func:`build_figure` instead.
    """
    build_figure(results)
    plt.show()
