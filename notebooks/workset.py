"""A working set of saved recordings: select once, analyse many ways.

``toolbox`` has the building blocks (catalog, sweep points, scope traces, plots). Using them
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

import html
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl
from toolbox import (
    PALETTE,
    SWEEP_KINDS,
    SWEEP_METRICS,
    array_inventory,
    array_overlay,
    best_points,
    csv_bytes,
    delta_to_reference,
    describe,
    field_chart,
    figure_bytes,
    pair_trace,
    pick,
    scan_catalog,
    scope_channels,
    scope_traces,
    scoreboard,
    sweep_points,
    verify,
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

    def subset(self, **filters: Any) -> Workset:
        """A smaller set, e.g. ``ws.subset(kind="frequency")``; same refs, same colours."""
        keep = pick(self.rows, **filters)
        clone = Workset.__new__(Workset)
        clone.rows = keep
        clone.missing = []
        clone._cache = {}
        clone._issues = {}
        clone._colors = self._colors  # a subset keeps its parent's colours
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
    ) -> Any:
        """:func:`toolbox.field_chart` with this set's run colours."""
        return field_chart(
            frame,
            x=x,
            y=y,
            color=color,
            detail=detail,
            facet=facet,
            mark=mark,
            title=title,
            palette=self.palette,
            x_log=x_log,
            y_log=y_log,
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
