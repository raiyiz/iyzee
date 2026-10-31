"""A working set of saved recordings: select once, analyse many ways.

The workbench package has the building blocks (catalog, sweep points, scope traces, plots). Using them
directly means re-filtering the catalog and re-threading the same frames through every
cell. A :class:`Workset` does that once::

    ws = Workset.open("demo-data", kind="frequency", latest=3)   # or pick by #ref
    ws.points                      # tidy table, one row per sweep point
    ws.scoreboard()                # which run was best, and where
    ws.plot(ws.points, x="offset_mhz", y="rel_noise_mean_db")
    ws.delta("#07")                # every other run minus run #07

Every run carries the stable ``#ref`` the catalog assigned it, and the ``run`` column of
every table (and every legend) starts with it, so it is always clear which recording a row
or a curve came from. A run also keeps one colour in every plot made from the same set.

Read-only: nothing here talks to an instrument. The notebook and scripts use this module
the same way.
"""

from __future__ import annotations

import contextlib
import html
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from .catalog import PALETTE, SWEEP_KINDS, SWEEP_METRICS, describe, pick, scan_catalog, verify
from .charts import csv_bytes, field_chart, figure_bytes
from .groups import group_curves, group_difference, group_summary
from .tables import (
    array_inventory,
    array_overlay,
    best_points,
    delta_to_reference,
    pair_trace,
    scope_channels,
    scope_traces,
    scoreboard,
    sweep_points,
)

ISSUE_SCHEMA = {"run": pl.String, "problem": pl.String}
POINT_TRACE_SCHEMA = {
    "run": pl.String,
    "run_id": pl.String,
    "synthetic": pl.Boolean,
    "point": pl.Int64,
    "sample": pl.Int64,
    "signal": pl.String,
    "value": pl.Float64,
}


class Workset:
    """The recordings you are working on, with every analysis one method away.

    Build one from a catalog with :meth:`from_catalog` (or straight from a directory with
    :meth:`open`). Tables are computed on first use and cached, so asking for ``ws.points``
    in five cells reads each file once.
    """

    def __init__(self, rows: pl.DataFrame, missing: Sequence[str] = ()) -> None:
        ordered = rows.sort("ref") if "ref" in rows.columns else rows
        # The plot/table label is "<ref> · <label>": the ref makes every legend entry unambiguous.
        self.rows = ordered.with_columns(
            (pl.col("ref") + " · " + pl.col("label")).alias("label"),
        )
        self.missing = list(missing)
        self._cache: dict[Any, Any] = {}
        self._issues: dict[str, list[dict[str, str]]] = {}
        self._colors: dict[str, str] = {
            label: PALETTE[index % len(PALETTE)]
            for index, label in enumerate(self.rows["label"].to_list())
        }
        self._groups: dict[str, str] = {}  # run_id -> group label

    # --- construction ---------------------------------------------------------------

    @classmethod
    def from_catalog(
        cls,
        catalog: pl.DataFrame,
        selection: Iterable[str] | None = None,
        *,
        strict: bool = True,
        **filters: Any,
    ) -> Workset:
        """Make a working set from catalog rows.

        ``selection`` items may be a ``#ref``, a path, a run id or a recording name (a name
        matches every recording with it). ``None`` means every row left after ``filters``
        (the keyword arguments of :func:`toolbox.pick`). With ``strict=False``, items that
        match nothing (a file deleted since the catalog was built) are listed in
        ``missing`` instead of raising.
        """
        frame = pick(catalog, **filters)
        if selection is None:
            return cls(frame)
        wanted = list(selection)
        known = {column: frame[column].to_list() for column in ("ref", "path", "run_id", "name")}
        hits: set[str] = set()
        missing = []
        for item in wanted:
            found = {
                path
                for column, values in known.items()
                for value, path in zip(values, known["path"], strict=True)
                if value == item
            }
            if found:
                hits |= found
            else:
                missing.append(item)
        if missing and strict:
            raise ValueError(f"not in the catalog: {', '.join(missing)}")
        return cls(frame.filter(pl.col("path").is_in(sorted(hits))), missing)

    @classmethod
    def open(
        cls, root: str | Path | None = None, selection: Iterable[str] | None = None, **filters: Any
    ) -> Workset:
        """Scan ``root`` (default: the configured data directory) and select from it."""
        from iyzee.config import data_root

        catalog = scan_catalog(Path(root) if root is not None else data_root())
        return cls.from_catalog(catalog, selection, **filters)

    @classmethod
    def from_saved(cls, catalog: pl.DataFrame, saved: SavedSet, *, strict: bool = False) -> Workset:
        """Rebuild a saved working set (and its groups) against the current catalog.

        Each saved run is found by its file path, else by its run id (so a recording that
        moved to another directory is still found). Runs that are gone are listed in
        ``missing`` (or raise with ``strict=True``). ``#refs`` are *not* used for matching:
        they number the whole catalog by age, so they shift if older recordings are added.
        """
        by_path = dict(zip(catalog["path"], catalog["path"], strict=True))
        by_run = dict(zip(catalog["run_id"], catalog["path"], strict=True))
        found: list[str] = []
        missing: list[str] = []
        for entry in saved.runs:
            path = by_path.get(entry.get("path", "")) or by_run.get(entry.get("run_id", ""))
            if path:
                found.append(path)
            else:
                missing.append(
                    f"{entry.get('ref', '?')} {entry.get('name') or entry.get('run_id', '')}"
                )
        if missing and strict:
            raise ValueError(f"not in the catalog: {', '.join(missing)}")
        ws = cls(catalog.filter(pl.col("path").is_in(found)), missing)
        return ws.with_groups(
            {run: group for run, group in saved.groups.items() if run in set(ws.rows["run_id"])}
        )

    def subset(self, **filters: Any) -> Workset:
        """A smaller set, e.g. ``ws.subset(kind="frequency")``; same refs, same colours."""
        keep = pick(self.rows, **filters)
        clone = Workset.__new__(Workset)
        clone.rows = keep
        clone.missing = []
        clone._cache = {}
        clone._issues = {}
        clone._colors = self._colors  # a subset keeps its parent's colours
        run_ids = set(clone.rows["run_id"].to_list())
        clone._groups = {run: group for run, group in self._groups.items() if run in run_ids}
        return clone

    # --- identity -------------------------------------------------------------------

    def __len__(self) -> int:
        return self.rows.height

    def __repr__(self) -> str:
        return f"Workset({', '.join(self.refs) or 'empty'})"

    @property
    def refs(self) -> list[str]:
        return self.rows["ref"].to_list()

    @property
    def paths(self) -> list[str]:
        return self.rows["path"].to_list()

    @property
    def palette(self) -> dict[str, str]:
        """Plot colour for each run label; the same in every figure made from this set.

        Colours follow the order of the set (and repeat after eight runs). A subset keeps its
        parent's colours; building a new set from different runs assigns new ones.
        """
        return {label: self._colors[label] for label in self.rows["label"].to_list()}

    def summary(self) -> dict[str, Any]:
        return describe(self.rows)

    def banner(self, title: str = "Working on", vertical: bool = False) -> str:
        """HTML strip naming every selected run with its plot colour (for any UI to show)."""
        if self.rows.is_empty():
            return (
                f"<div><b>{html.escape(title)}:</b> <i>nothing selected yet - "
                "pick recordings in section 2.</i></div>"
            )
        colors = self.palette
        chips = []
        for row in self.rows.iter_rows(named=True):
            swatch = (
                f'<span style="display:inline-block;width:0.8em;height:0.8em;border-radius:50%;'
                f'background:{colors[row["label"]]};margin-right:0.35em"></span>'
            )
            chips.append(
                f'<span style="margin-right:1.1em;white-space:nowrap">{swatch}'
                f"<b>{html.escape(row['ref'])}</b> {html.escape(row['name'] or row['kind'])}"
                f' <span style="opacity:.65">({row["kind"]}'
                f"{', synthetic' if row['synthetic'] else ''})</span></span>"
            )
        note = " <i>(colours repeat beyond 8 runs)</i>" if len(self) > len(PALETTE) else ""
        glue = "<br>" if vertical else ""
        head = f"<b>{html.escape(title)}:</b>" + ("<br>" if vertical else " ")
        return f"<div>{head}{glue.join(chips)}{note}</div>"

    # --- tables ---------------------------------------------------------------------

    def _once(self, key: Any, build: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    @property
    def sweeps(self) -> pl.DataFrame:
        return self.rows.filter(pl.col("kind").is_in(SWEEP_KINDS))

    @property
    def scopes(self) -> pl.DataFrame:
        return self.rows.filter(pl.col("kind") == "scope")

    @property
    def points(self) -> pl.DataFrame:
        """One row per sweep point of every selected sweep (frequency and bandwidth)."""

        def build() -> pl.DataFrame:
            issues: list[dict[str, str]] = []
            frame = sweep_points(self.sweeps, issues=issues)
            self._issues["sweeps"] = issues
            return frame

        return self._once("points", build)

    @property
    def channel_stats(self) -> pl.DataFrame:
        """Per-channel statistics stored with each selected scope acquisition."""
        return self._once("channel_stats", lambda: scope_channels(self.scopes))

    @property
    def channels(self) -> list[str]:
        stats = self.channel_stats
        return sorted(stats["channel"].unique().to_list()) if not stats.is_empty() else []

    def scoreboard(self, metric: str = "rel_noise_mean_db") -> pl.DataFrame:
        """Best point, mean and count below shot noise for each sweep, best first."""
        return scoreboard(self.points, metric)

    def best(self, metric: str = "rel_noise_mean_db", kind: str | None = None) -> pl.DataFrame:
        """The lowest ``metric`` point of each run (optionally one sweep kind)."""
        points = self.points if kind is None else self.points.filter(pl.col("kind") == kind)
        return best_points(points, metric)

    def traces(self, channels: Sequence[str] | None = None, budget: int = 1600) -> pl.DataFrame:
        """Scope waveforms for ``channels`` (default: all), thinned to ``budget`` per trace."""
        wanted = tuple(self.channels if channels is None else channels)

        def build() -> pl.DataFrame:
            issues: list[dict[str, str]] = []
            frame = scope_traces(self.scopes, wanted, budget=budget, issues=issues)
            self._issues["traces"] = issues
            return frame

        return self._once(("traces", wanted, budget), build)

    def point_traces(self, point: int = 0) -> pl.DataFrame:
        """Raw squeezing and shot-noise traces of sweep point ``point`` for every sweep.

        Long table: ``signal`` is ``squeezing``, ``shot noise`` (both dBm) or
        ``relative noise`` (dB). Sweeps with no such point are noted in :attr:`issues`.
        """

        def build() -> pl.DataFrame:
            parts = []
            issues = []
            for row in self.sweeps.iter_rows(named=True):
                try:
                    wide = pair_trace(row, point)
                except Exception as exc:  # noqa: BLE001 - isolate a damaged archive
                    issues.append({"run": row["label"], "problem": f"point {point}: {exc}"})
                    continue
                if wide.is_empty():
                    issues.append({"run": row["label"], "problem": f"has no point {point}"})
                    continue
                for column, signal in (
                    ("squeezing_dbm", "squeezing"),
                    ("shot_noise_dbm", "shot noise"),
                    ("rel_noise_db", "relative noise"),
                ):
                    parts.append(
                        wide.select(
                            pl.lit(row["label"]).alias("run"),
                            pl.lit(row["run_id"]).alias("run_id"),
                            pl.lit(row["synthetic"]).alias("synthetic"),
                            pl.lit(point, dtype=pl.Int64).alias("point"),
                            pl.col("sample"),
                            pl.lit(signal).alias("signal"),
                            pl.col(column).alias("value"),
                        )
                    )
            self._issues["point_traces"] = issues
            return pl.concat(parts) if parts else pl.DataFrame(schema=POINT_TRACE_SCHEMA)

        return self._once(("point_traces", point), build)

    def array_inventory(self) -> pl.DataFrame:
        """Name, shape and dtype of every saved array in every selected run."""

        def build() -> pl.DataFrame:
            issues: list[dict[str, str]] = []
            frame = array_inventory(self.rows, issues=issues)
            self._issues["inventory"] = issues
            return frame

        return self._once("inventory", build)

    @property
    def array_names(self) -> list[str]:
        inventory = self.array_inventory()
        return sorted(inventory["array"].unique().to_list()) if not inventory.is_empty() else []

    def array(self, name: str, index: int = 0, budget: int | None = 4000) -> pl.DataFrame:
        """Saved array ``name`` (row ``index`` if 2-D) of every run that has it."""

        def build() -> pl.DataFrame:
            issues: list[dict[str, str]] = []
            frame = array_overlay(self.rows, name, index, budget, issues)
            self._issues["array"] = issues
            return frame

        return self._once(("array", name, index, budget), build)

    def delta(
        self,
        reference: str | None = None,
        kind: str | None = None,
        metric: str = "rel_noise_mean_db",
    ) -> pl.DataFrame:
        """Every other run of one sweep kind minus a reference run, on the shared x range.

        ``reference`` is a ``#ref``, run id or label; it defaults to the first run.
        ``kind`` defaults to the only sweep kind present. Positive means noisier.
        """
        kinds = self.points["kind"].unique().to_list()
        if kind is None:
            if len(kinds) != 1:
                raise ValueError(f"choose kind= from {sorted(kinds)}")
            kind = kinds[0]
        points = self.points.filter(pl.col("kind") == kind)
        if points.is_empty():
            raise ValueError(f"no {kind} sweeps in this working set")
        runs = points.unique("run_id", maintain_order=True)
        if reference is None:
            run_id = runs["run_id"][0]
        else:
            refs = dict(zip(self.rows["ref"], self.rows["run_id"], strict=True))
            labels = dict(zip(self.rows["label"], self.rows["run_id"], strict=True))
            run_id = refs.get(reference) or labels.get(reference) or reference
            if run_id not in set(runs["run_id"]):
                raise ValueError(f"{reference!r} is not a {kind} sweep in this working set")
        return delta_to_reference(points, run_id, metric)

    # --- groups and statistics ------------------------------------------------------

    def _clone(self) -> Workset:
        clone = Workset.__new__(Workset)
        clone.rows = self.rows
        clone.missing = list(self.missing)
        clone._cache = self._cache  # same runs, so cached tables stay valid
        clone._issues = self._issues
        clone._colors = self._colors
        clone._groups = dict(self._groups)
        return clone

    def with_groups(
        self,
        spec: Mapping[str, Any] | str | Callable[[dict[str, Any]], str | None],
    ) -> Workset:
        """A copy of this set whose runs are labelled with group names.

        ``spec`` can be:

        * ``{"#02": "A", "#05": "B"}``: run (``#ref``, run id, path or recording name) -> group;
        * ``{"pumped": ["#05", "#09"], "baseline": ["#02"]}``: group -> runs;
        * a column name such as ``"name"``, ``"kind"``, ``"status"`` or ``"synthetic"``;
        * a function of the catalog row returning a group name (or ``None`` for ungrouped).

        Empty names mean "no group". Runs not mentioned stay ungrouped. Unknown runs raise.
        """
        rows = self.rows.to_dicts()
        assigned: dict[str, str] = {}
        pairs: list[tuple[str, Any]]
        if callable(spec):
            pairs = [(row["run_id"], spec(row)) for row in rows]
        elif isinstance(spec, str):
            if spec not in self.rows.columns:
                raise ValueError(f"cannot group by unknown column {spec!r}")
            pairs = [(row["run_id"], row[spec]) for row in rows]
        else:
            pairs = []
            for key, value in spec.items():
                if isinstance(value, str):  # run -> group
                    pairs.extend(self._resolve(key, value))
                else:  # group -> runs
                    for run_key in value:
                        pairs.extend(self._resolve(run_key, key))
        for run_id, group in pairs:
            label = "" if group is None else str(group).strip()
            if label:
                assigned[run_id] = label
        clone = self._clone()
        clone._groups = assigned
        return clone

    def _resolve(self, key: str, group: str) -> list[tuple[str, str]]:
        hits = {
            row["run_id"]
            for row in self.rows.iter_rows(named=True)
            if key in (row["ref"], row["run_id"], row["path"], row["name"])
        }
        if not hits:
            raise ValueError(f"not in this working set: {key!r}")
        return [(run_id, group) for run_id in hits]

    @property
    def group_map(self) -> dict[str, str]:
        """``run_id -> group`` for the grouped runs (a copy)."""
        return dict(self._groups)

    @property
    def groups(self) -> pl.DataFrame:
        """Which run is in which group (ungrouped runs are left out)."""
        rows = [
            {
                "group": self._groups[row["run_id"]],
                "ref": row["ref"],
                "run": row["label"],
                "run_id": row["run_id"],
            }
            for row in self.rows.iter_rows(named=True)
            if row["run_id"] in self._groups
        ]
        schema = {"group": pl.String, "ref": pl.String, "run": pl.String, "run_id": pl.String}
        return pl.DataFrame(rows, schema=schema).sort(["group", "ref"])

    @property
    def group_names(self) -> list[str]:
        return sorted(set(self._groups.values()))

    @property
    def group_colors(self) -> dict[str, str]:
        """One colour per group, assigned in alphabetical order of the group names."""
        return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(self.group_names)}

    def group_summary(self, metric: str = "rel_noise_mean_db") -> pl.DataFrame:
        """Run count per group and the mean/spread of each run's best point."""
        return group_summary(self.scoreboard(metric), self._groups)

    def group_stats(
        self, metric: str = "rel_noise_mean_db", kind: str | None = None, band: str = "std"
    ) -> pl.DataFrame:
        """Mean curve (with spread) of each group on one shared x grid; see :func:`toolbox.group_curves`."""
        return group_curves(self.points, self._groups, metric, kind, band)

    def group_diff(
        self, a: str, b: str, metric: str = "rel_noise_mean_db", kind: str | None = None
    ) -> pl.DataFrame:
        """Group ``a`` minus group ``b`` with standard error; see :func:`toolbox.group_difference`."""
        return group_difference(self.points, self._groups, a, b, metric, kind)

    def plot_groups(
        self, metric: str = "rel_noise_mean_db", kind: str | None = None, band: str = "std"
    ) -> Any:
        """Group mean curves with their spread band, one colour per group."""
        stats = self.group_stats(metric, kind, band)
        if stats.is_empty():
            raise ValueError(
                "no grouped sweep has usable points on a shared x range (label some sweeps first)"
            )
        x = "offset_mhz" if stats["kind"][0] == "frequency" else "rbw_khz"
        label = {"std": "±1 s.d.", "sem": "±1 s.e.", "minmax": "min to max", "none": "no band"}[
            band
        ]
        return field_chart(
            stats.with_columns(
                (pl.col("group") + " (n=" + pl.col("n").cast(pl.String) + ")").alias("group_n")
            ),
            x=x,
            y=metric,
            color="group_n",
            title=f"Group means ({label})",
            palette={
                f"{name} (n={n})": self.group_colors[name]
                for name, n in stats.unique("group").select("group", "n").iter_rows()
            },
            x_log=stats["kind"][0] == "bandwidth",
            band=None if band == "none" else ("band_low", "band_high"),
        )

    def plot_group_diff(
        self, a: str, b: str, metric: str = "rel_noise_mean_db", kind: str | None = None
    ) -> Any:
        """Difference of two group means with a ±2 standard-error band."""
        diff = self.group_diff(a, b, metric, kind)
        x = "offset_mhz" if diff["kind"][0] == "frequency" else "rbw_khz"
        return field_chart(
            diff,
            x=x,
            y="delta",
            color=None,
            title=f"{a} minus {b} (band: ±2 standard errors)",
            x_log=diff["kind"][0] == "bandwidth",
            band=("delta_low", "delta_high"),
        )

    # --- problems, integrity, export ------------------------------------------------

    @property
    def issues(self) -> pl.DataFrame:
        """Files that could not be read so far (one row per run and problem)."""
        _ = self.points
        _ = self.channel_stats
        rows = [issue for group in self._issues.values() for issue in group]
        return pl.DataFrame(rows, schema=ISSUE_SCHEMA).unique(maintain_order=True)

    def verify(self) -> pl.DataFrame:
        """Check each file's SHA-256 against its sidecar."""
        return verify(self.rows)

    @staticmethod
    def csv(frame: pl.DataFrame) -> bytes:
        return csv_bytes(frame)

    @staticmethod
    def png(figure: Any, fmt: str = "png") -> bytes:
        return figure_bytes(figure, fmt)

    # --- plots ----------------------------------------------------------------------

    def plot(
        self,
        frame: pl.DataFrame,
        *,
        x: str,
        y: str,
        color: str | None = "run",
        detail: str | None = None,
        facet: str | None = None,
        mark: str = "line",
        title: str = "",
        x_log: bool = False,
        y_log: bool = False,
        palette: Mapping[str, str] | None = None,
        band: tuple[str, str] | None = None,
    ) -> Any:
        """:func:`toolbox.field_chart` with this set's run colours (``palette`` overrides them)."""
        return field_chart(
            frame,
            x=x,
            y=y,
            color=color,
            detail=detail,
            facet=facet,
            mark=mark,
            title=title,
            palette=self.palette if palette is None else palette,
            x_log=x_log,
            y_log=y_log,
            band=band,
        )

    def quick_plots(
        self,
        metric: str = "rel_noise_mean_db",
        channels: Sequence[str] | None = None,
        budget: int = 1600,
        facet_channels: bool = True,
    ) -> list[tuple[str, Any]]:
        """The standard comparison figures, as ``(heading, figure)`` pairs.

        One figure per sweep kind present, plus one for the scope waveforms. Empty when the
        set holds nothing that can be drawn.
        """
        if metric not in SWEEP_METRICS:
            raise ValueError(f"unsupported sweep metric: {metric!r}")
        figures: list[tuple[str, Any]] = []
        for kind, x, heading in (
            ("frequency", "offset_mhz", "Frequency sweeps · shared frequency reference"),
            ("bandwidth", "rbw_khz", "Bandwidth sweeps"),
        ):
            part = self.points.filter(pl.col("kind") == kind)
            if not part.is_empty():
                figures.append(
                    (
                        heading,
                        self.plot(part, x=x, y=metric, x_log=kind == "bandwidth"),
                    )
                )
        traces = self.traces(channels, budget)
        if not traces.is_empty():
            figures.append(
                (
                    "Scope captures",
                    self.plot(
                        traces,
                        x="time",
                        y="value",
                        color="run",
                        detail=None if facet_channels else "channel",
                        facet="channel" if facet_channels else None,
                        title="Scope waveforms",
                    ),
                )
            )
        return figures

    def tables(self) -> Mapping[str, pl.DataFrame]:
        """Named tables for pickers and exports (computed on first use)."""
        return {
            "Run scoreboard": self.scoreboard(),
            "Frequency sweep points": self.points.filter(pl.col("kind") == "frequency"),
            "Bandwidth sweep points": self.points.filter(pl.col("kind") == "bandwidth"),
            "Scope channel statistics": self.channel_stats,
            "Scope waveform samples": self.traces(),
        }


class SavedSet:
    """One stored working set: which recordings, and how they were grouped."""

    def __init__(
        self,
        name: str,
        saved: str,
        runs: Sequence[Mapping[str, str]],
        groups: Mapping[str, str],
    ) -> None:
        self.name = name
        self.saved = saved
        self.runs = [dict(run) for run in runs]
        self.groups = dict(groups)

    def __repr__(self) -> str:
        return f"SavedSet({self.name!r}, {len(self.runs)} runs, {len(self.groups)} grouped)"


def default_store_path() -> Path:
    """``worksets.json`` next to iyzee's config file (override: ``IYZEE_WORKSETS_FILE``).

    It deliberately lives with the user's settings, never inside the data directory, so the
    recordings folder stays strictly read-only for analysis.
    """
    override = os.environ.get("IYZEE_WORKSETS_FILE")
    if override:
        return Path(override).expanduser()
    from iyzee.config import config_file

    return config_file().parent / "worksets.json"


class WorksetStore:
    """Named working sets kept in one small JSON file.

    ::

        store = WorksetStore()
        store.save("repeat scans", ws)                  # remembers the runs and any groups
        ws = Workset.from_saved(catalog, store.get("repeat scans"))

    Sets are matched back to recordings by file path (then run id), not by ``#ref``. Writes
    are atomic. A file that cannot be parsed is set aside as ``worksets.json.corrupt``
    rather than overwritten, and ``problem`` explains it.
    """

    VERSION = 1

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_store_path()
        self.problem: str | None = None

    def _read(self) -> dict[str, dict[str, Any]]:
        self.problem = None
        try:
            payload = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.problem = f"cannot read {self.path}: {exc}"
            return {}
        sets = payload.get("sets") if isinstance(payload, dict) else None
        if not isinstance(sets, dict):
            self.problem = f"{self.path} is not a saved-sets file"
            return {}
        return {name: entry for name, entry in sets.items() if isinstance(entry, dict)}

    def names(self) -> list[str]:
        return sorted(self._read(), key=str.casefold)

    def describe(self) -> pl.DataFrame:
        """One row per saved set: name, when, how many runs and grouped runs."""
        rows = [
            {
                "name": name,
                "saved": str(entry.get("saved", "")),
                "runs": len(entry.get("runs", [])),
                "grouped": len(entry.get("groups", {})),
            }
            for name, entry in self._read().items()
        ]
        schema = {"name": pl.String, "saved": pl.String, "runs": pl.Int64, "grouped": pl.Int64}
        return pl.DataFrame(rows, schema=schema).sort("name")

    def get(self, name: str) -> SavedSet:
        entries = self._read()
        if name not in entries:
            raise KeyError(f"no saved working set named {name!r}")
        entry = entries[name]
        runs = [r for r in entry.get("runs", []) if isinstance(r, dict)]
        groups = entry.get("groups", {})
        return SavedSet(
            name,
            str(entry.get("saved", "")),
            runs,
            groups if isinstance(groups, dict) else {},
        )

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("give the working set a name")
        if len(cleaned) > 80:
            raise ValueError("working-set names are limited to 80 characters")
        return cleaned

    def save(self, name: str, ws: Workset, groups: Mapping[str, str] | None = None) -> SavedSet:
        """Store ``ws`` under ``name`` (replacing a set of that name).

        ``groups`` (``run_id -> group``) defaults to the set's own groups; entries for runs
        that are not in the set are dropped.
        """
        name = self._clean_name(name)
        if len(ws) == 0:
            raise ValueError("the working set is empty; add recordings before saving it")
        in_set = set(ws.rows["run_id"].to_list())
        chosen = ws.group_map if groups is None else dict(groups)
        entry = SavedSet(
            name,
            datetime.now().astimezone().isoformat(timespec="seconds"),
            [
                {k: row[k] or "" for k in ("path", "run_id", "ref", "name")}
                for row in ws.rows.iter_rows(named=True)
            ],
            {run: str(g).strip() for run, g in chosen.items() if run in in_set and str(g).strip()},
        )
        entries = self._read()
        if self.problem and self.path.exists():
            self.path.replace(self.path.with_name(self.path.name + ".corrupt"))
            self.problem = f"{self.problem} (moved aside as {self.path.name}.corrupt)"
        entries[name] = {"saved": entry.saved, "runs": entry.runs, "groups": entry.groups}
        self._write(entries)
        return entry

    def delete(self, name: str) -> bool:
        """Remove a saved set; returns whether there was one."""
        entries = self._read()
        if name not in entries:
            return False
        del entries[name]
        self._write(entries)
        return True

    def _write(self, entries: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": self.VERSION, "sets": entries}, indent=2, sort_keys=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".worksets-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w") as stream:
                stream.write(payload + "\n")
            Path(temp).replace(self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(temp).unlink()
            raise
