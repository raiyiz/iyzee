"""Read-only helpers for browsing, comparing, and plotting saved iyzee recordings.

The notebook is the interface; these functions stay ordinary Python so they can also
be reused in scripts and IPython. Only the small JSON manifests are read for the catalog.
Waveform arrays are loaded only for recordings you mark for analysis.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from functools import lru_cache
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

# Colour-blind-safe (Okabe-Ito based) cycle. Plots give every run one colour from this list.
PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#555555",
    "#B8A100",
)
LINESTYLES = ("-", "--", ":", "-.")

CATALOG_SCHEMA = {
    "run_id": pl.String,
    "ref": pl.String,
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


def read_manifest(path: Path | str) -> dict[str, Any]:
    """The JSON sidecar of a recording as a dict (empty if missing or malformed)."""
    return _manifest(Path(path))


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
        prefix = "SYNTHETIC · " if row["synthetic"] else ""
        base = f"{prefix}{row['name'] or row['kind']} · {when}"
        counts[base] = counts.get(base, 0) + 1
        labels.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return pl.Series("label", labels, dtype=pl.String)


def _refs(frame: pl.DataFrame) -> pl.Series:
    """Short chronological ids (``#01`` is the oldest recording in the catalog).

    They depend on the whole catalog, not on a filter or a selection, so a run keeps its
    ``#ref`` in every table, legend and plot while you change what you are looking at.
    """
    started = frame["started"].to_list()
    paths = frame["path"].to_list()
    order = sorted(
        range(frame.height),
        key=lambda i: (started[i] is not None, started[i] or datetime.min, paths[i]),
    )
    width = max(2, len(str(frame.height)))
    refs = [""] * frame.height
    for rank, index in enumerate(order, start=1):
        refs[index] = f"#{rank:0{width}d}"
    return pl.Series("ref", refs, dtype=pl.String)


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
                "ref": "",
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
    return frame.with_columns(_unique_labels(frame), _refs(frame))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str | bytes) or not isinstance(value, Sequence | set | frozenset):
        return [value]
    return list(value)


def pick(
    catalog: pl.DataFrame,
    *,
    kind: str | Sequence[str] | None = None,
    text: str = "",
    status: str | Sequence[str] | None = None,
    synthetic: bool | None = None,
    since: date | datetime | None = None,
    until: date | datetime | None = None,
    refs: Sequence[str] | None = None,
    latest: int | None = None,
) -> pl.DataFrame:
    """Filter catalog rows; every argument left at its default keeps everything.

    The notebook's filter bar and scripts share this one function, so "the 3 latest
    frequency sweeps that completed" reads the same in both::

        pick(catalog, kind="frequency", status="completed", latest=3)

    ``kind`` and ``status`` accept one value or several (``"all"`` means no filter).
    ``text`` is a case-insensitive substring match over name, status, kind, instrument and
    path. ``since``/``until`` compare with the start time (dates cover the whole day).
    ``latest`` keeps the newest N rows left after the other filters, in catalog order.
    """
    frame = catalog
    kinds = [k for k in _as_list(kind) if k != "all"]
    if kinds:
        frame = frame.filter(pl.col("kind").is_in(kinds))
    statuses = [v for v in _as_list(status) if v != "all"]
    if statuses:
        frame = frame.filter(pl.col("status").is_in(statuses))
    if synthetic is not None:
        frame = frame.filter(pl.col("synthetic") == synthetic)
    if refs:
        frame = frame.filter(pl.col("ref").is_in(list(refs)))
    if since is not None:
        start = (
            since if isinstance(since, datetime) else datetime.combine(since, datetime.min.time())
        )
        frame = frame.filter(pl.col("started") >= start)
    if until is not None:
        end = until if isinstance(until, datetime) else datetime.combine(until, datetime.max.time())
        frame = frame.filter(pl.col("started") <= end)
    query = text.strip().lower()
    if query:
        frame = frame.filter(
            pl.any_horizontal(
                *(
                    pl.col(column).str.to_lowercase().str.contains(query, literal=True)
                    for column in ("label", "status", "kind", "name", "instrument", "path")
                )
            )
        )
    if latest is not None and latest < frame.height:
        newest = frame.sort("started", descending=True, nulls_last=True).head(latest)
        frame = frame.filter(pl.col("path").is_in(newest["path"].to_list()))
    return frame


def default_selection(root: Path, count: int = 3) -> list[str]:
    """Paths of the newest recordings, found from file names alone (no sidecar is read).

    Files are written to ``YYYY-MM/DDTHHMMSS_*.npz``, so sorting the paths sorts by time.
    This is only a cheap starting point for a notebook; it is not the catalog's ordering.
    """
    root = root.expanduser()
    if not root.is_dir() or count <= 0:
        return []
    return [str(path.resolve()) for path in sorted(root.rglob("*.npz"))[-count:]]


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
    output = []
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
    output = []
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


def _title(column: str) -> str:
    known = {
        **SWEEP_METRICS,
        "offset_mhz": "Laser frequency offset (MHz; shared reference)",
        "rbw_khz": "Resolution bandwidth (kHz)",
        "x_thz": "Measured laser frequency (THz)",
        "time": "Time (ms)",
        "sample": "Sample index",
        "delta_db": "Δ relative noise vs reference (dB)",
        "delta": "Difference between groups (A minus B)",
        "se": "Standard error",
        "group": "Group",
        "best_db": "Best relative noise (dB)",
        "best_x": "Position of best point",
        "mean_db": "Mean relative noise (dB)",
        "rel_noise_db": "Relative noise (dB)",
        "value": "Amplitude",
        "rms": "RMS amplitude",
        "minimum": "Minimum amplitude",
        "maximum": "Maximum amplitude",
        "stddev": "Standard deviation",
        "peak_to_peak": "Peak-to-peak amplitude",
    }
    return known.get(column, column.replace("_", " ").capitalize())


BAND_CHOICES = ("std", "sem", "minmax", "none")


def _sweep_axis(kind: str) -> tuple[str, bool]:
    """x column of a sweep kind and whether it is naturally logarithmic."""
    if kind not in SWEEP_KINDS:
        raise ValueError(f"unsupported sweep kind: {kind!r}")
    return ("offset_mhz", False) if kind == "frequency" else ("rbw_khz", True)


def _common_grid(curves: list[tuple[np.ndarray, np.ndarray]], log: bool) -> np.ndarray:
    """One x grid every curve covers: their shared x values, else an even grid over the overlap."""
    low = max(float(x.min()) for x, _ in curves)
    high = min(float(x.max()) for x, _ in curves)
    if low >= high:
        return np.array([])
    first = curves[0][0]
    if all(len(x) == len(first) and np.allclose(x, first, rtol=1e-6, atol=1e-9) for x, _ in curves):
        return first[(first >= low) & (first <= high)]
    count = max(int(np.median([len(x) for x, _ in curves])), 2)
    return np.geomspace(low, high, count) if log and low > 0 else np.linspace(low, high, count)


def _group_matrices(
    points: pl.DataFrame,
    groups: Mapping[str, str],
    metric: str,
    kind: str | None,
) -> tuple[str, str, np.ndarray, dict[str, np.ndarray]]:
    """Every grouped run of one sweep kind interpolated onto one shared grid.

    ``groups`` maps ``run_id`` to a group label. Returns ``(kind, x column, grid, matrices)``
    where ``matrices[group]`` has one row per run of that group. Runs with fewer than two
    usable points are skipped; interpolation is linear (in log x for bandwidth sweeps) and
    never extrapolates, so the grid is limited to the range *all* grouped runs cover.
    """
    if metric not in SWEEP_METRICS:
        raise ValueError(f"unsupported sweep metric: {metric!r}")
    grouped = points.filter(pl.col("run_id").is_in(list(groups)))
    kinds = sorted(grouped["kind"].unique().to_list()) if not grouped.is_empty() else []
    if kind is None:
        if len(kinds) > 1:
            raise ValueError(f"choose kind= from {kinds}")
        kind = kinds[0] if kinds else "frequency"
    x, log = _sweep_axis(kind)
    part = grouped.filter(pl.col("kind") == kind).drop_nulls([x, metric]).sort(x)
    curves: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for run_id in part["run_id"].unique(maintain_order=True):
        run = part.filter(pl.col("run_id") == run_id)
        if run.height >= 2:
            curves[run_id] = (run[x].to_numpy(), run[metric].to_numpy())
    if not curves:
        return kind, x, np.array([]), {}
    grid = _common_grid(list(curves.values()), log)
    if grid.size == 0:
        return kind, x, grid, {}
    matrices: dict[str, list[np.ndarray]] = {}
    for run_id, (xs, ys) in curves.items():
        values = (
            np.interp(np.log(grid), np.log(xs), ys)
            if log and xs.min() > 0
            else np.interp(grid, xs, ys)
        )
        matrices.setdefault(groups[run_id], []).append(values)
    return kind, x, grid, {name: np.vstack(rows) for name, rows in matrices.items()}


def group_curves(
    points: pl.DataFrame,
    groups: Mapping[str, str],
    metric: str = "rel_noise_mean_db",
    kind: str | None = None,
    band: str = "std",
) -> pl.DataFrame:
    """Mean curve of each group of runs, with a spread band, on one shared x grid.

    ``groups`` maps ``run_id`` to a group label. Per grid point the table has the group's run
    count ``n``, the mean (named after ``metric`` so axis labels stay meaningful), ``std``
    (sample, needs two runs), ``sem``, ``min``/``max`` and ``band_low``/``band_high`` for the
    chosen ``band`` (``std``, ``sem``, ``minmax`` or ``none``). A group with one run has a
    mean but no spread.
    """
    if band not in BAND_CHOICES:
        raise ValueError(f"band must be one of {BAND_CHOICES}, not {band!r}")
    kind, x, grid, matrices = _group_matrices(points, groups, metric, kind)
    schema = {
        "group": pl.String,
        "kind": pl.String,
        x: pl.Float64,
        "n": pl.Int64,
        metric: pl.Float64,
        "std": pl.Float64,
        "sem": pl.Float64,
        "min": pl.Float64,
        "max": pl.Float64,
        "band_low": pl.Float64,
        "band_high": pl.Float64,
    }
    frames = []
    for name in sorted(matrices):
        matrix = matrices[name]
        n = matrix.shape[0]
        mean = matrix.mean(axis=0)
        std = matrix.std(axis=0, ddof=1) if n > 1 else np.full(grid.shape, np.nan)
        sem = std / np.sqrt(n)
        low, high = {
            "std": (mean - std, mean + std),
            "sem": (mean - sem, mean + sem),
            "minmax": (matrix.min(axis=0), matrix.max(axis=0)),
            "none": (np.full(grid.shape, np.nan),) * 2,
        }[band]
        frames.append(
            pl.DataFrame(
                {
                    "group": name,
                    "kind": kind,
                    x: grid,
                    "n": n,
                    metric: mean,
                    "std": std,
                    "sem": sem,
                    "min": matrix.min(axis=0),
                    "max": matrix.max(axis=0),
                    "band_low": low,
                    "band_high": high,
                }
            ).with_columns(pl.col(pl.Float64).fill_nan(None), pl.col("n").cast(pl.Int64))
        )
    return pl.concat(frames) if frames else pl.DataFrame(schema=schema)


def group_difference(
    points: pl.DataFrame,
    groups: Mapping[str, str],
    a: str,
    b: str,
    metric: str = "rel_noise_mean_db",
    kind: str | None = None,
) -> pl.DataFrame:
    """Group ``a`` minus group ``b`` at every shared x, with its standard error.

    Uses Welch's unequal-variance form: ``se = sqrt(var_a/n_a + var_b/n_b)`` and
    ``t = delta / se``. ``significant`` flags ``|t| > 2``, a rough "bigger than the run-to-run
    scatter" guide for moderate group sizes. It is not a p-value, and with fewer than three
    runs per group it says little. Both groups need at least two runs to have a spread; rows
    without one carry nulls in ``se``/``t`` and are not flagged. Positive ``delta`` means ``a``
    is higher (noisier, for the noise metrics).
    """
    kind, x, grid, matrices = _group_matrices(points, groups, metric, kind)
    for name in (a, b):
        if name not in matrices:
            raise ValueError(f"group {name!r} has no usable runs (groups: {sorted(matrices)})")
    left, right = matrices[a], matrices[b]
    mean_a, mean_b = left.mean(axis=0), right.mean(axis=0)
    delta = mean_a - mean_b
    n_a, n_b = left.shape[0], right.shape[0]
    if n_a > 1 and n_b > 1:
        se = np.sqrt(left.var(axis=0, ddof=1) / n_a + right.var(axis=0, ddof=1) / n_b)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(se > 0, delta / se, np.where(delta == 0, 0.0, np.inf * np.sign(delta)))
    else:
        se = np.full(grid.shape, np.nan)
        t = np.full(grid.shape, np.nan)
    return pl.DataFrame(
        {
            "kind": kind,
            x: grid,
            "mean_a": mean_a,
            "mean_b": mean_b,
            "delta": delta,
            "se": se,
            "t": t,
            "delta_low": delta - 2 * se,
            "delta_high": delta + 2 * se,
            "n_a": n_a,
            "n_b": n_b,
            "significant": np.abs(t) > 2,
        }
    ).with_columns(pl.col(pl.Float64).fill_nan(None), pl.col("n_a", "n_b").cast(pl.Int64))


def group_summary(board: pl.DataFrame, groups: Mapping[str, str]) -> pl.DataFrame:
    """Per group and sweep kind: run count and the spread of each run's best point.

    Built from :func:`scoreboard`, so ``best_x`` is only meaningful within one kind.
    """
    schema = {
        "group": pl.String,
        "kind": pl.String,
        "runs": pl.Int64,
        "best_db_mean": pl.Float64,
        "best_db_std": pl.Float64,
        "best_x_mean": pl.Float64,
        "best_x_std": pl.Float64,
        "below_shot_noise_mean": pl.Float64,
    }
    if board.is_empty() or not groups:
        return pl.DataFrame(schema=schema)
    labelled = board.with_columns(
        pl.col("run_id").replace_strict(groups, default=None, return_dtype=pl.String).alias("group")
    ).drop_nulls("group")
    if labelled.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        labelled.group_by(["group", "kind"], maintain_order=True)
        .agg(
            pl.len().alias("runs"),
            pl.col("best_db").mean().alias("best_db_mean"),
            pl.col("best_db").std().alias("best_db_std"),
            pl.col("best_x").mean().alias("best_x_mean"),
            pl.col("best_x").std().alias("best_x_std"),
            pl.col("below_shot_noise").mean().alias("below_shot_noise_mean"),
        )
        .sort(["kind", "group"])
    )


def _tick_text(value: Any) -> str:
    return (
        f"{value:g}"
        if isinstance(value, int | float) and not isinstance(value, bool)
        else str(value)
    )


def field_chart(
    frame: pl.DataFrame,
    *,
    x: str,
    y: str,
    color: str | None = "run",
    detail: str | None = None,
    facet: str | None = None,
    mark: str = "line",
    title: str = "",
    palette: Mapping[str, str] | None = None,
    x_log: bool = False,
    y_log: bool = False,
    band: tuple[str, str] | None = None,
) -> Any:
    """Build a reusable Matplotlib figure from any tidy Polars table.

    Choose columns instead of maintaining a one-off plot per experiment:

    * ``color`` and ``detail`` split the rows into series. ``color`` picks the hue (looked up
      in ``palette`` when the value is there, so a run keeps its colour across figures);
      ``detail`` varies the line style, e.g. run = colour, channel = dashes.
    * ``facet`` draws one panel per value, with a shared x axis.
    * ``mark`` is ``"line"``, ``"points"`` or ``"bars"`` (grouped side by side per x value).
    * ``band=("low_col", "high_col")`` shades between two columns behind each line (a
      spread or uncertainty band around a mean).

    The figure is not registered with pyplot, so repeated calls do not accumulate; save it
    with :func:`figure_bytes` or ``figure.savefig``.
    """
    from matplotlib.figure import Figure

    band_columns = list(band) if band else []
    for column in (x, y, color, detail, facet, *band_columns):
        if column is not None and column not in frame.columns:
            raise ValueError(f"column {column!r} is not in the selected table")
    columns = list(
        dict.fromkeys(c for c in (x, y, color, detail, facet, *band_columns) if c is not None)
    )
    data = frame.select(columns).drop_nulls([x, y]).sort(x)
    if data.is_empty():
        raise ValueError("no usable rows for these plot fields")

    groups = list(dict.fromkeys(c for c in (color, detail) if c is not None and c not in {x, y}))
    panels = data[facet].unique(maintain_order=True).to_list() if facet else [None]
    ncols = min(len(panels), 3)
    nrows = math.ceil(len(panels) / ncols)

    # Colours are fixed per colour-value for the whole figure, whatever the panel.
    hues: dict[Any, str] = {}
    styles: dict[Any, str] = {}
    for record in data.select(groups).iter_rows() if groups else []:
        key = dict(zip(groups, record, strict=True))
        if color in key and key[color] not in hues:
            fallback = PALETTE[len(hues) % len(PALETTE)]
            hues[key[color]] = (palette or {}).get(key[color], fallback)
        if detail in key and key[detail] not in styles:
            styles[key[detail]] = LINESTYLES[len(styles) % len(LINESTYLES)]

    categories = list(dict.fromkeys(data[x].to_list())) if mark == "bars" else []
    figure = Figure(figsize=(6.2 * ncols + 3.2, 4.2 * nrows + 0.6), layout="constrained")
    axes = figure.subplots(nrows, ncols, squeeze=False, sharex=True)
    handles: dict[str, Any] = {}

    for slot, panel in zip(axes.flat, panels, strict=False):
        part = data.filter(pl.col(facet) == panel) if facet else data
        buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for record in part.to_dicts():
            buckets.setdefault(tuple(record[g] for g in groups), []).append(record)
        for position, (key, records) in enumerate(buckets.items()):
            named = dict(zip(groups, key, strict=True))
            label = " · ".join(str(v) for v in key) or None
            style = {
                "color": hues.get(named.get(color)) if color else None,
                "label": label,
            }
            xs = [r[x] for r in records]
            ys = [r[y] for r in records]
            if mark == "points":
                artist = slot.scatter(xs, ys, s=26, alpha=0.85, **style)
            elif mark == "bars":
                width = 0.8 / len(buckets)
                shifted = [
                    categories.index(v) + (position - (len(buckets) - 1) / 2) * width for v in xs
                ]
                artist = slot.bar(shifted, ys, width=width, alpha=0.85, **style)
            else:
                if band:
                    shaded = [
                        r for r in records if r[band[0]] is not None and r[band[1]] is not None
                    ]
                    if shaded:
                        slot.fill_between(
                            [r[x] for r in shaded],
                            [r[band[0]] for r in shaded],
                            [r[band[1]] for r in shaded],
                            color=style["color"],
                            alpha=0.18,
                            linewidth=0,
                        )
                (artist,) = slot.plot(
                    xs,
                    ys,
                    marker="o" if len(xs) <= 80 else None,
                    markersize=3.5,
                    linewidth=1.5,
                    linestyle=styles.get(named.get(detail), "-"),
                    **style,
                )
            if label and label not in handles:
                handles[label] = artist
        if mark == "bars":
            slot.set_xticks(range(len(categories)), [_tick_text(c) for c in categories])
        if x_log:
            slot.set_xscale("log")
        if y_log:
            slot.set_yscale("log")
        if y.startswith(("rel_noise", "delta")):
            slot.axhline(0, linestyle="--", linewidth=1, alpha=0.7, color="black")
        slot.grid(True, which="major", alpha=0.25)
        slot.spines[["top", "right"]].set_visible(False)
        slot.set_xlabel(_title(x))
        slot.set_ylabel(_title(y))
        if facet:
            slot.set_title(f"{facet}: {panel}", loc="left", fontsize="small")
    for slot in list(axes.flat)[len(panels) :]:
        slot.set_visible(False)

    heading = title or f"{_title(y)} by {_title(x)}"
    if facet:
        figure.suptitle(heading, x=0.01, ha="left", fontsize="large")
    else:
        axes[0, 0].set_title(heading, loc="left", pad=10)
    if handles:
        figure.legend(
            list(handles.values()),
            list(handles),
            loc="outside right upper",
            frameon=False,
            fontsize="small",
        )
    return figure


def figure_bytes(figure: Any, fmt: str = "png", dpi: int = 200) -> bytes:
    """Serialise a figure (``png``, ``svg`` or ``pdf``) for a download button or a file."""
    import io

    if fmt not in {"png", "svg", "pdf"}:
        raise ValueError(f"unsupported figure format: {fmt!r}")
    buffer = io.BytesIO()
    figure.savefig(buffer, format=fmt, dpi=dpi)
    return buffer.getvalue()


def csv_bytes(frame: pl.DataFrame) -> bytes:
    """CSV bytes suitable for a marimo download control."""
    return frame.write_csv().encode("utf-8")
