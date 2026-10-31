"""Compare sweep groups on a shared, non-extrapolated grid."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import polars as pl

from .catalog import SWEEP_KINDS, SWEEP_METRICS


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
