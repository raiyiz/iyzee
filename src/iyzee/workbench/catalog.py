"""Catalog saved recordings and provide stable, composable selections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from iyzee.experiment.io import STEM_PATTERN

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
