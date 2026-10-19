"""Read-only helpers for browsing, comparing, and plotting saved iyzee recordings.

The notebook is the interface; these functions stay ordinary Python so they can also
be reused in scripts and IPython. Only the small JSON manifests are read for the catalog.
Waveform arrays are loaded only for recordings you mark for analysis.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from iyzee.analysis import summarize_sweep
from iyzee.experiment import Recording, load_recording
from iyzee.experiment.io import STEM_PATTERN
from iyzee.waveform_math import traces_from_scope_recording

SWEEP_KINDS = ("frequency", "bandwidth")
KINDS = (*SWEEP_KINDS, "scope", "unknown")

SWEEP_METRICS = {
    "rel_noise_mean_db": "Relative noise · mean (dB)",
    "rel_noise_min_db": "Relative noise · minimum (dB)",
    "squeezing_dbm": "Squeezing level · mean (dBm)",
    "shot_noise_dbm": "Shot-noise level · mean (dBm)",
}
SCOPE_METRICS = ("rms", "peak_to_peak", "mean", "minimum", "maximum", "stddev")

CATALOG_SCHEMA = {
    "run_id": pl.String,
    "label": pl.String,
    "name": pl.String,
    "kind": pl.String,
    "started": pl.Datetime,
    "status": pl.String,
    "items": pl.Int64,
    "instrument": pl.String,
    "synthetic": pl.Boolean,
    "warnings": pl.Int64,
    "size_kib": pl.Float64,
    "digest": pl.String,
    "path": pl.String,
}


def _manifest(path: Path) -> dict[str, Any]:
    """Return a sidecar object; a missing or malformed sidecar is not fatal."""
    try:
        value = json.loads(path.with_suffix(".json").read_text())
    except OSError, ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _started(path: Path, manifest: dict[str, Any]) -> datetime | None:
    run = _mapping(manifest.get("run_metadata"))
    stamp = manifest.get("started_at_utc") or run.get("started_at_utc")
    if isinstance(stamp, str):
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass

    # Files written by iyzee use data/YYYY-MM/DDTHHMMSS_name_hash.npz.
    try:
        year, month = (int(part) for part in path.parent.name.split("-")[:2])
        day, clock = path.stem.split("_", 1)[0].split("T")
        return datetime(year, month, int(day), int(clock[:2]), int(clock[2:4]), int(clock[4:6]))
    except ValueError, IndexError:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None


def _kind(path: Path, manifest: dict[str, Any]) -> str:
    if manifest.get("kind") == "scope-acquisition" or manifest.get("waveforms"):
        return "scope"
    points = manifest.get("points")
    if isinstance(points, list) and points:
        if any(
            isinstance(point, dict)
            and ("wavemeter_channel" in point or "measured_frequency_thz" in point)
            for point in points
        ):
            return "frequency"
        return "bandwidth"
    for kind in SWEEP_KINDS:
        if f"_{kind}_" in path.name or path.stem.endswith(f"_{kind}"):
            return kind
    return "unknown"


def _unique_labels(frame: pl.DataFrame) -> pl.Series:
    labels: list[str] = []
    counts: dict[str, int] = {}
    for row in frame.iter_rows(named=True):
        when = row["started"].strftime("%b %d %H:%M") if row["started"] else "undated"
        base = f"{row['name'] or row['kind']} · {when}"
        counts[base] = counts.get(base, 0) + 1
        labels.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return pl.Series("label", labels, dtype=pl.String)


def scan_catalog(root: Path) -> pl.DataFrame:
    """Index each saved NPZ once, using sidecars only (no waveform arrays are loaded)."""
    root = root.expanduser()
    paths = sorted(root.rglob("*.npz")) if root.is_dir() else []
    rows = []
    for path in paths:
        manifest = _manifest(path)
        kind = _kind(path, manifest)
        run = _mapping(manifest.get("run_metadata"))
        instrument = _mapping(manifest.get("instrument"))
        acquisition = _mapping(manifest.get("acquisition"))
        points = manifest.get("points") if kind in SWEEP_KINDS else manifest.get("waveforms")
        match = STEM_PATTERN.match(path.stem)
        try:
            size_kib = path.stat().st_size / 1024
        except OSError:
            size_kib = None
        warning_values = acquisition.get("warnings") or manifest.get("warnings") or []
        warning_count = len(warning_values) if isinstance(warning_values, list) else 0
        rows.append(
            {
                "run_id": path.stem,
                "label": "",
                "name": match["name"] if match else "",
                "kind": kind,
                "started": _started(path, manifest),
                "status": str(run.get("status") or ("acquired" if kind == "scope" else "unknown")),
                "items": len(points) if isinstance(points, list) else None,
                "instrument": str(
                    instrument.get("identity") or manifest.get("instrument_id") or ""
                ),
                "synthetic": bool(run.get("synthetic") or manifest.get("synthetic")),
                "warnings": warning_count,
                "size_kib": size_kib,
                "digest": "recorded" if manifest.get("data_sha256") else "missing",
                "path": str(path.resolve()),
            }
        )

    frame = pl.DataFrame(rows, schema=CATALOG_SCHEMA)
    if frame.is_empty():
        return frame
    frame = frame.sort("started", descending=True, nulls_last=True)
    return frame.with_columns(_unique_labels(frame))


def describe(rows: pl.DataFrame) -> dict[str, Any]:
    """Compact overview values for a marked set of catalog rows."""
    if rows.is_empty():
        return {"runs": 0, "items": 0, "synthetic": 0, "by_kind": {}}
    return {
        "runs": rows.height,
        "items": int(rows["items"].fill_null(0).sum()),
        "synthetic": int(rows["synthetic"].sum()),
        "by_kind": dict(rows.group_by("kind").len().sort("kind").iter_rows()),
    }


def verify(rows: pl.DataFrame) -> pl.DataFrame:
    """Compare each marked NPZ against the SHA-256 recorded in its JSON sidecar."""
    results = []
    for row in rows.iter_rows(named=True):
        path = Path(row["path"])
        expected = _manifest(path).get("data_sha256")
        if not expected:
            state = "no digest recorded"
        else:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1 << 20), b""):
                        digest.update(block)
                state = "matches" if digest.hexdigest() == expected else "MISMATCH"
            except OSError as exc:
                state = f"unreadable ({exc.strerror})"
        results.append({"run": row["label"], "integrity": state})
    return pl.DataFrame(results, schema={"run": pl.String, "integrity": pl.String})


def _load(row: dict[str, Any]) -> Recording:
    return load_recording(Path(row["path"]))


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
    schema = {"run": pl.String, "run_id": pl.String, x: pl.Float64, "delta_db": pl.Float64}
    if reference.is_empty():
        return pl.DataFrame(schema=schema)
    ref_x = reference[x].to_numpy()
    ref_y = reference[metric].to_numpy()
    rows = []
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
    output = []
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


def scope_traces(
    rows: pl.DataFrame,
    channels: Sequence[str],
    budget: int = 1600,
    issues: list[dict[str, str]] | None = None,
) -> pl.DataFrame:
    """Long waveform table for selected channels, lightly decimated for responsive plotting."""
    output = []
    for row in rows.filter(pl.col("kind") == "scope").iter_rows(named=True):
        try:
            traces = traces_from_scope_recording(_load(row))
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


def _title(column: str) -> str:
    known = {
        **SWEEP_METRICS,
        "offset_mhz": "Laser frequency offset (MHz; shared reference)",
        "rbw_khz": "Resolution bandwidth (kHz)",
        "x_thz": "Measured laser frequency (THz)",
        "time": "Time",
        "sample": "Sample index",
        "value": "Amplitude",
        "rms": "RMS amplitude",
        "minimum": "Minimum amplitude",
        "maximum": "Maximum amplitude",
        "stddev": "Standard deviation",
        "peak_to_peak": "Peak-to-peak amplitude",
    }
    return known.get(column, column.replace("_", " ").capitalize())


def field_chart(
    frame: pl.DataFrame,
    *,
    x: str,
    y: str,
    color: str | None = "run",
    detail: str | None = None,
    mark: str = "line",
    title: str = "",
) -> Any:
    """Build a reusable Matplotlib plot from any tidy Polars table.

    Choose columns instead of maintaining a one-off plot per experiment. Colour and detail
    split the selected rows into separate series; the returned figure can be saved or composed.
    """
    import matplotlib.pyplot as plt

    for column in (x, y, color, detail):
        if column is not None and column not in frame.columns:
            raise ValueError(f"column {column!r} is not in the selected table")
    columns = list(dict.fromkeys(column for column in (x, y, color, detail) if column is not None))
    data = frame.select(columns).drop_nulls([x, y]).sort(x)
    if data.is_empty():
        raise ValueError("no usable rows for these plot fields")

    groups = [column for column in (color, detail) if column is not None and column not in {x, y}]
    groups = list(dict.fromkeys(groups))
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in data.to_dicts():
        key = tuple(row[column] for column in groups)
        buckets.setdefault(key, []).append(row)

    figure, axis = plt.subplots(figsize=(9, 4.8), layout="constrained")
    for key, records in buckets.items():
        label = (
            " · ".join(f"{column}: {value}" for column, value in zip(groups, key, strict=True))
            or None
        )
        xs = [row[x] for row in records]
        ys = [row[y] for row in records]
        if mark == "points":
            axis.scatter(xs, ys, label=label, s=26, alpha=0.85)
        elif mark == "bars":
            axis.bar(xs, ys, label=label, alpha=0.8)
        else:
            axis.plot(xs, ys, marker="o", markersize=3.5, linewidth=1.5, label=label)

    if y.startswith("rel_noise"):
        axis.axhline(0, linestyle="--", linewidth=1, alpha=0.7)
    axis.set_title(title or f"{_title(y)} by {_title(x)}", loc="left", pad=10)
    axis.set_xlabel(_title(x))
    axis.set_ylabel(_title(y))
    axis.grid(True, which="major", alpha=0.25)
    axis.spines[["top", "right"]].set_visible(False)
    if groups:
        axis.legend(frameon=False, fontsize="small")
    return figure


def csv_bytes(frame: pl.DataFrame) -> bytes:
    """CSV bytes suitable for a marimo download control."""
    return frame.write_csv().encode("utf-8")
