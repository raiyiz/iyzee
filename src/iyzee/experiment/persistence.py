"""Measurement persistence: create run directories and save step results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .step import StepResult


def create_dirs(name: str = "") -> Path:
    """Create and return today's measurement-data directory."""
    package_root = Path(__file__).resolve().parent.parent
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    data_dir = package_root / "data" / today / name
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
