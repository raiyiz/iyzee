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
    *,
    path: Path | None = None,
    **extra_arrays: Any,
) -> Path:
    """Save variable-length trace data in a compressed NumPy archive.

    ``metadata``, if given, is a per-point list of JSON-serializable dicts
    (same length/order as ``data``) describing the instrument state that
    produced each point. ``extra_arrays`` lets callers attach additional
    top-level arrays (e.g. run-level metadata) without another signature
    change later.

    By default a new timestamped file is created in ``savedir``. Pass
    ``path`` (a file returned by an earlier call) to overwrite that file
    instead — how a long run checkpoints itself after every point without
    littering the directory with one file per point.

    The write is atomic: the archive is written next to its destination
    and moved into place, so a crash or power cut mid-write leaves the
    previous checkpoint intact rather than a truncated ``.npz``.
    """
    if path is None:
        timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        path = savedir / f"{timestamp}.npz"
    arrays: dict[str, Any] = {"data": np.asarray(data, dtype=object)}
    if metadata is not None:
        arrays["metadata"] = np.asarray(metadata, dtype=object)
    arrays.update(extra_arrays)
    # ".part", not ".npz.tmp": np.savez appends ".npz" to names that lack it
    # (writing through a file object avoids that), and Traces globs *.npz,
    # so an in-flight file must not match.
    partial = path.with_name(path.name + ".part")
    with partial.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    partial.replace(path)
    return path


def save_step_results(
    results: list[StepResult],
    savedir: Path,
    run_metadata: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
) -> Path:
    """Save a list of :class:`StepResult` with their per-point metadata.

    This is the preferred way to persist an experiment run: unlike the raw
    ``save_data()`` tuples, the saved archive is self-describing — every
    point carries the instrument state that produced it, and the run as a
    whole can carry a software revision, analyzer config, and timestamp via
    ``run_metadata``. ``path`` overwrites an earlier save instead of creating
    a new file (see :func:`save_data`).
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
    return save_data(data, savedir, metadata=per_point_meta, path=path, **extra)


def difference_series(
    squeezing: object,
    shot_noise: object,
    label: str | None,
    *,
    sweep_duration_ms: float | None = None,
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
    if sweep_duration_ms is None:
        x_values = list(range(len(difference)))
    else:
        x_values = np.linspace(0.0, sweep_duration_ms, len(difference)).tolist()
    return x_values, list(difference), label


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
        sweep_duration_ms = result.meta.get("sweep_duration_ms")
        series = difference_series(
            result.traces.get("squeezing"),
            result.traces.get("shot_noise"),
            result.label,
            sweep_duration_ms=float(sweep_duration_ms) if sweep_duration_ms is not None else None,
        )
        if series is None:
            continue
        _x, difference, label = series
        ax.plot(difference)
        labels.append(label)

    if labels:
        ax.legend(labels, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.1))
    ax.set_xlabel("Sweep time (ms)")
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
