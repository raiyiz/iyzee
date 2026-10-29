"""Build tidy analysis tables from saved iyzee recordings."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from iyzee.analysis import summarize_sweep
from iyzee.experiment import Recording, load_recording
from iyzee.waveform_math import traces_from_scope_recording

from .catalog import SWEEP_KINDS, SWEEP_METRICS, _manifest, _mapping


@lru_cache(maxsize=6)
def _cached_recording(path: str, mtime_ns: int, size: int) -> Recording:
    """Read each file once per file version, so several views of a run share one load."""
    return load_recording(Path(path))


def _load(row: dict[str, Any]) -> Recording:
    path = Path(row["path"])
    stat = path.stat()
    return _cached_recording(str(path), stat.st_mtime_ns, stat.st_size)


def _trace_level(array: np.ndarray | None, index: int) -> float:
    if array is None or index >= len(array):
        return math.nan
    values = np.asarray(array[index], dtype=float)
    values = values[np.isfinite(values)]
    return float(values.mean()) if values.size else math.nan


def sweep_points(rows: pl.DataFrame, issues: list[dict[str, str]] | None = None) -> pl.DataFrame:
    """Long table: one row per sweep point from the selected recordings.

    Frequency offsets use one shared median reference across all marked frequency runs;
    individual runs are not re-centred, so real frequency drift remains visible.
    """
    output: list[dict[str, Any]] = []
    for row in rows.filter(pl.col("kind").is_in(SWEEP_KINDS)).iter_rows(named=True):
        try:
            recording = _load(row)
            means = summarize_sweep(recording, "mean").points
            minima = summarize_sweep(recording, "minimum").points
            squeezing = recording.arrays.get("trace_squeezing")
            shot_noise = recording.arrays.get("trace_shot_noise")
            for mean, minimum in zip(means, minima, strict=True):
                frequency = row["kind"] == "frequency"
                output.append(
                    {
                        "run": row["label"],
                        "run_id": row["run_id"],
                        "synthetic": row["synthetic"],
                        "kind": row["kind"],
                        "point": mean.index,
                        "point_label": mean.label,
                        "requested": mean.requested,
                        "measured_thz": mean.measured if frequency else None,
                        "x_thz": mean.x if frequency else None,
                        "offset_mhz": None,
                        "rbw_khz": mean.requested / 1000 if not frequency else None,
                        "rel_noise_mean_db": mean.relative_noise_db,
                        "rel_noise_min_db": minimum.relative_noise_db,
                        "squeezing_dbm": _trace_level(squeezing, mean.index),
                        "shot_noise_dbm": _trace_level(shot_noise, mean.index),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - isolate a damaged archive from other selected runs
            if issues is not None:
                issues.append({"run": row["label"], "problem": f"could not read sweep: {exc}"})

    frequencies = [
        row["x_thz"]
        for row in output
        if row["kind"] == "frequency" and row["x_thz"] is not None and math.isfinite(row["x_thz"])
    ]
    reference = float(np.median(frequencies)) if frequencies else 0.0
    for row in output:
        if row["kind"] == "frequency" and row["x_thz"] is not None:
            row["offset_mhz"] = (row["x_thz"] - reference) * 1e6

    schema = {
        "run": pl.String,
        "run_id": pl.String,
        "synthetic": pl.Boolean,
        "kind": pl.String,
        "point": pl.Int64,
        "point_label": pl.String,
        "requested": pl.Float64,
        "measured_thz": pl.Float64,
        "x_thz": pl.Float64,
        "offset_mhz": pl.Float64,
        "rbw_khz": pl.Float64,
        "rel_noise_mean_db": pl.Float64,
        "rel_noise_min_db": pl.Float64,
        "squeezing_dbm": pl.Float64,
        "shot_noise_dbm": pl.Float64,
    }
    return pl.DataFrame(output, schema=schema)


def best_points(points: pl.DataFrame, metric: str = "rel_noise_mean_db") -> pl.DataFrame:
    """Return each run's lowest (best) value of a sweep metric, sorted best first."""
    if metric not in SWEEP_METRICS:
        raise ValueError(f"unsupported sweep metric: {metric!r}")
    if points.is_empty():
        return points
    return (
        points.drop_nulls(metric)
        .sort(metric)
        .group_by("run_id", maintain_order=True)
        .first()
        .sort(metric)
    )


def scoreboard(points: pl.DataFrame, metric: str = "rel_noise_mean_db") -> pl.DataFrame:
    """One row per sweep: the quick "which run was best, and where" table.

    ``best_x`` is the laser offset (MHz) for frequency sweeps and the RBW (kHz) for
    bandwidth sweeps, so it only compares between runs of the same ``kind``.
    """
    if metric not in SWEEP_METRICS:
        raise ValueError(f"unsupported sweep metric: {metric!r}")
    schema = {
        "run": pl.String,
        "run_id": pl.String,
        "kind": pl.String,
        "synthetic": pl.Boolean,
        "points": pl.Int64,
        "best_db": pl.Float64,
        "best_x": pl.Float64,
        "mean_db": pl.Float64,
        "below_shot_noise": pl.Int64,
    }
    rows = []
    for part in points.partition_by("run_id", maintain_order=True):
        usable = part.drop_nulls(metric)
        if usable.is_empty():
            continue
        best = usable.sort(metric).row(0, named=True)
        x = best["offset_mhz"] if best["kind"] == "frequency" else best["rbw_khz"]
        rows.append(
            {
                "run": best["run"],
                "run_id": best["run_id"],
                "kind": best["kind"],
                "synthetic": best["synthetic"],
                "points": usable.height,
                "best_db": best[metric],
                "best_x": x,
                "mean_db": float(usable[metric].mean()),  # type: ignore[arg-type]
                "below_shot_noise": int((usable[metric] < 0).sum()),
            }
        )
    return pl.DataFrame(rows, schema=schema).sort("best_db", nulls_last=True)


def delta_to_reference(
    points: pl.DataFrame, reference_run_id: str, metric: str = "rel_noise_mean_db"
) -> pl.DataFrame:
    """Interpolate other runs onto a reference run's x grid; no extrapolation.

    Positive values mean the comparison run has more relative noise than the reference.
    """
    if metric not in SWEEP_METRICS:
        raise ValueError(f"unsupported sweep metric: {metric!r}")
    kinds = points["kind"].unique().to_list()
    if len(kinds) > 1:
        raise ValueError("compare only one sweep kind at a time")
    x = "offset_mhz" if kinds == ["frequency"] else "rbw_khz"
    reference = points.filter(pl.col("run_id") == reference_run_id).drop_nulls([x, metric]).sort(x)
    schema = {
        "run": pl.String,
        "run_id": pl.String,
        "synthetic": pl.Boolean,
        x: pl.Float64,
        "delta_db": pl.Float64,
    }
    if reference.is_empty():
        return pl.DataFrame(schema=schema)
    ref_x = reference[x].to_numpy()
    ref_y = reference[metric].to_numpy()
    rows: list[dict[str, Any]] = []
    for run_id in points["run_id"].unique(maintain_order=True):
        if run_id == reference_run_id:
            continue
        other = points.filter(pl.col("run_id") == run_id).drop_nulls([x, metric]).sort(x)
        if other.height < 2:
            continue
        other_x = other[x].to_numpy()
        inside = (ref_x >= other_x.min()) & (ref_x <= other_x.max())
        if not inside.any():
            continue
        interpolated = np.interp(ref_x[inside], other_x, other[metric].to_numpy())
        rows.extend(
            {
                "run": other["run"][0],
                "run_id": run_id,
                "synthetic": bool(other["synthetic"][0]),
                x: float(x_value),
                "delta_db": float(delta),
            }
            for x_value, delta in zip(ref_x[inside], interpolated - ref_y[inside], strict=True)
        )
    return pl.DataFrame(rows, schema=schema)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def scope_channels(rows: pl.DataFrame) -> pl.DataFrame:
    """Long table of per-channel statistics stored in each scope manifest."""
    output: list[dict[str, Any]] = []
    for row in rows.filter(pl.col("kind") == "scope").iter_rows(named=True):
        manifest = _manifest(Path(row["path"]))
        configuration = _mapping(manifest.get("configuration"))
        settings = {
            str(item.get("channel")): item
            for item in (configuration.get("applied_channel_settings") or [])
            if isinstance(item, dict)
        }
        for waveform in manifest.get("waveforms") or []:
            if not isinstance(waveform, dict):
                continue
            channel = str(waveform.get("channel") or "?")
            stats = _mapping(waveform.get("stats"))
            setting = settings.get(channel, {})
            output.append(
                {
                    "run": row["label"],
                    "run_id": row["run_id"],
                    "synthetic": row["synthetic"],
                    "channel": channel,
                    "unit": str(waveform.get("value_unit") or ""),
                    "samples": stats.get("sample_count")
                    if isinstance(stats.get("sample_count"), int)
                    else None,
                    "minimum": _finite_number(stats.get("min")),
                    "maximum": _finite_number(stats.get("max")),
                    "peak_to_peak": _finite_number(stats.get("peak_to_peak")),
                    "mean": _finite_number(stats.get("mean")),
                    "rms": _finite_number(stats.get("rms")),
                    "stddev": _finite_number(stats.get("stddev")),
                    "volts_per_div": _finite_number(setting.get("volts_per_div")),
                    "coupling": setting.get("coupling"),
                    "warnings": row["warnings"],
                }
            )
    schema = {
        "run": pl.String,
        "run_id": pl.String,
        "synthetic": pl.Boolean,
        "channel": pl.String,
        "unit": pl.String,
        "samples": pl.Int64,
        "minimum": pl.Float64,
        "maximum": pl.Float64,
        "peak_to_peak": pl.Float64,
        "mean": pl.Float64,
        "rms": pl.Float64,
        "stddev": pl.Float64,
        "volts_per_div": pl.Float64,
        "coupling": pl.String,
        "warnings": pl.Int64,
    }
    return pl.DataFrame(output, schema=schema)


def decimate(
    time: np.ndarray, values: np.ndarray, budget: int = 1600
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce plot size while keeping both extrema in every bucket."""
    count = min(len(time), len(values))
    time, values = time[:count], values[:count]
    if budget < 4 or len(values) <= budget:
        return time, values
    edges = np.linspace(0, len(values), budget // 2 + 1, dtype=int)
    keep = []
    for lo, hi in pairwise(edges):
        if hi <= lo:
            continue
        window = values[lo:hi]
        finite = np.isfinite(window)
        if not finite.any():
            keep.append(lo)
            continue
        low = lo + int(np.nanargmin(np.where(finite, window, np.inf)))
        high = lo + int(np.nanargmax(np.where(finite, window, -np.inf)))
        keep.extend(sorted({low, high}))
    index = np.asarray(keep, dtype=int)
    return time[index], values[index]


@lru_cache(maxsize=4)
def _cached_scope_traces(path: str, mtime_ns: int, size: int) -> tuple[Any, ...]:
    """Decode a scope file once per file version; reuse traces when display controls change."""
    return tuple(traces_from_scope_recording(load_recording(Path(path))))


def scope_traces(
    rows: pl.DataFrame,
    channels: Sequence[str],
    budget: int = 1600,
    issues: list[dict[str, str]] | None = None,
) -> pl.DataFrame:
    """Long waveform table for selected channels, lightly decimated for responsive plotting."""
    output: list[dict[str, Any]] = []
    for row in rows.filter(pl.col("kind") == "scope").iter_rows(named=True):
        try:
            path = Path(row["path"])
            stat = path.stat()
            traces = _cached_scope_traces(str(path), stat.st_mtime_ns, stat.st_size)
        except Exception as exc:  # noqa: BLE001 - isolate a damaged archive from other selected runs
            if issues is not None:
                issues.append({"run": row["label"], "problem": f"could not read scope: {exc}"})
            continue
        for trace in traces:
            if trace.label not in channels:
                continue
            time, values = decimate(trace.time, trace.values, budget)
            seconds = trace.time_unit.lower() in {"s", "sec", "seconds"}
            output.extend(
                {
                    "run": row["label"],
                    "run_id": row["run_id"],
                    "synthetic": row["synthetic"],
                    "channel": trace.label,
                    "time": float(t * 1000 if seconds else t),
                    "time_unit": "ms" if seconds else trace.time_unit,
                    "value": float(value),
                    "unit": trace.value_unit,
                }
                for t, value in zip(time, values, strict=True)
            )
    return pl.DataFrame(
        output,
        schema={
            "run": pl.String,
            "run_id": pl.String,
            "synthetic": pl.Boolean,
            "channel": pl.String,
            "time": pl.Float64,
            "time_unit": pl.String,
            "value": pl.Float64,
            "unit": pl.String,
        },
    )


def channels_in(rows: pl.DataFrame) -> list[str]:
    """Channels available in the selected scope recordings."""
    frame = scope_channels(rows)
    return sorted(frame["channel"].unique().to_list()) if not frame.is_empty() else []


def pair_trace(row: dict[str, Any], point: int) -> pl.DataFrame:
    """The raw squeezing and shot-noise traces for one selected sweep point."""
    recording = _load(row)
    squeezing = recording.arrays.get("trace_squeezing")
    shot = recording.arrays.get("trace_shot_noise")
    if squeezing is None or shot is None or point >= len(squeezing) or point >= len(shot):
        return pl.DataFrame(
            schema={
                "sample": pl.Int64,
                "squeezing_dbm": pl.Float64,
                "shot_noise_dbm": pl.Float64,
                "rel_noise_db": pl.Float64,
            }
        )
    sqz = np.asarray(squeezing[point], dtype=float)
    sn = np.asarray(shot[point], dtype=float)
    count = min(len(sqz), len(sn))
    difference = sqz[:count] - sn[:count]
    return pl.DataFrame(
        {
            "sample": np.arange(count),
            "squeezing_dbm": sqz[:count],
            "shot_noise_dbm": sn[:count],
            "rel_noise_db": difference,
        }
    )


def array_frame(array: np.ndarray, index: int = 0) -> pl.DataFrame:
    """Make a 1-D array, or one row of a 2-D array, ready to plot or export.

    Two-dimensional recordings commonly store one trace per sweep point. Higher
    dimensions are rejected rather than silently flattening away their meaning.
    """
    values = np.asarray(array)
    if values.ndim == 1:
        if index != 0:
            raise ValueError("index only applies to two-dimensional arrays")
        selected = values
    elif values.ndim == 2:
        if not 0 <= index < values.shape[0]:
            raise ValueError(f"row index {index} is outside an array with {values.shape[0]} rows")
        selected = values[index]
    else:
        raise ValueError(f"expected a 1-D or 2-D array, got shape {values.shape}")

    selected = np.asarray(selected, dtype=float)
    return pl.DataFrame({"sample": np.arange(selected.size, dtype=np.int64), "value": selected})


def preview_facts(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Human-readable facts about one catalog row, for a quick look before selecting it."""
    started = row.get("started")
    size = row.get("size_kib")
    return [
        ("Reference", f"{row['ref']}  ({row['run_id']})"),
        ("Type", f"{row['kind']}" + (" · SYNTHETIC" if row["synthetic"] else " · measured")),
        ("Started", started.strftime("%Y-%m-%d %H:%M:%S") if started else "unknown"),
        ("Status", str(row["status"])),
        ("Instrument", str(row["instrument"]) or "not recorded"),
        ("Items", "unknown" if row["items"] is None else str(row["items"])),
        ("Warnings", str(row["warnings"])),
        ("Size", "unknown" if size is None else f"{size:,.0f} KiB"),
        ("Checksum", str(row["digest"])),
        ("File", str(row["path"])),
    ]


def array_inventory(rows: pl.DataFrame, issues: list[dict[str, str]] | None = None) -> pl.DataFrame:
    """Every saved array of every run (name, shape, dtype), to see what a file holds."""
    output: list[dict[str, Any]] = []
    for row in rows.iter_rows(named=True):
        try:
            recording = _load(row)
        except Exception as exc:  # noqa: BLE001 - isolate a damaged archive from other selected runs
            if issues is not None:
                issues.append({"run": row["label"], "problem": f"could not read arrays: {exc}"})
            continue
        output.extend(
            {
                "run": row["label"],
                "run_id": row["run_id"],
                "array": name,
                "shape": " x ".join(str(n) for n in array.shape) or "scalar",
                "ndim": array.ndim,
                "dtype": str(array.dtype),
            }
            for name, array in sorted(recording.arrays.items())
        )
    return pl.DataFrame(
        output,
        schema={
            "run": pl.String,
            "run_id": pl.String,
            "array": pl.String,
            "shape": pl.String,
            "ndim": pl.Int64,
            "dtype": pl.String,
        },
    )


def array_overlay(
    rows: pl.DataFrame,
    name: str,
    index: int = 0,
    budget: int | None = 4000,
    issues: list[dict[str, str]] | None = None,
) -> pl.DataFrame:
    """One saved array (a row of it, when 2-D) from every run that has it, as a long table.

    Runs without the array are skipped quietly; runs with fewer rows than ``index`` (or an
    unreadable file) are skipped and reported in ``issues`` instead of stopping the others.
    """
    output = []
    for row in rows.iter_rows(named=True):
        try:
            recording = _load(row)
            if name not in recording.arrays:
                continue  # a different kind of recording: nothing to overlay, not an error
            frame = array_frame(recording.arrays[name], index=index)
        except Exception as exc:  # noqa: BLE001 - isolate a damaged archive from other selected runs
            if issues is not None:
                issues.append({"run": row["label"], "problem": f"{name}: {exc}"})
            continue
        sample = frame["sample"].to_numpy().astype(float)
        value = frame["value"].to_numpy()
        if budget:
            sample, value = decimate(sample, value, budget)
        output.append(
            pl.DataFrame(
                {
                    "run": row["label"],
                    "run_id": row["run_id"],
                    "synthetic": row["synthetic"],
                    "array": name,
                    "array_index": index,
                    "sample": sample.astype(np.int64),
                    "value": value,
                }
            )
        )
    schema = {
        "run": pl.String,
        "run_id": pl.String,
        "synthetic": pl.Boolean,
        "array": pl.String,
        "array_index": pl.Int64,
        "sample": pl.Int64,
        "value": pl.Float64,
    }
    return pl.concat(output) if output else pl.DataFrame(schema=schema)
