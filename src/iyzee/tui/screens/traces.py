"""Traces screen: browse persisted sweep and scope recordings.

Sweep recordings are treated as experiments rather than just collections of
arrays: the run overview is plotted against its sweep variable, the points
are tabulated, and moving through the point table shows the corresponding raw
squeezing/shot-noise traces. Scope recordings keep their existing waveform
preview.

Loading a run is still done off the UI thread. The selected recording is
retained for sweep navigation, while generation guards prevent an old load
from overwriting a newer one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.markup import escape
from textual.widgets import DataTable, Label, ListItem, ListView, Select, Static
from textual_plotext import PlotextPlot

from ...experiment import difference_values_many
from ...experiment.io import DATA_ROOT, STEM_PATTERN, Recording, load_recording
from ..plotting import draw_series, prepare_series
from .page import Page

# Kept separate so tests can redirect the results directory without touching
# the persistence module's global.
_DATA_ROOT = DATA_ROOT

_STATISTICS = (
    ("Mean delta", "mean"),
    ("Minimum delta", "minimum"),
)


def _run_label(path: Path, mtime: float) -> str:
    """List label for one saved run: its name (if any) and save time."""
    when = datetime.fromtimestamp(mtime).astimezone()
    match = STEM_PATTERN.match(path.stem)
    name = match["name"] if match else None
    prefix = f"{escape(name)}  " if name else ""
    return f"{prefix}{when:%Y-%m-%d %H:%M:%S}"


@dataclass(frozen=True)
class _PreparedView:
    """Data prepared off the UI thread for one selected recording."""

    summary: str
    series: Sequence[tuple[Sequence[float], Sequence[float], str | None]] | None
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    recording: Recording | None = None


def _prepare_scope_view(path: Path, arrays: dict[str, np.ndarray], metadata: dict) -> _PreparedView:
    """Build the durable scope preview without touching widgets."""
    lines = [f"[b]{escape(path.name)}[/b]", "Scope acquisition"]
    if metadata.get("measurement_id"):
        lines.append(f"Measurement: {escape(str(metadata['measurement_id']))}")
    if metadata.get("started_at_utc"):
        lines.append(f"Started: {escape(str(metadata['started_at_utc']))}")
    instrument = metadata.get("instrument")
    if isinstance(instrument, dict):
        lines.append("Instrument: " + escape(str(instrument.get("address") or "address unknown")))
    series: list[tuple[list[float], list[float], str | None]] = []
    for waveform in metadata.get("waveforms", []):
        if not isinstance(waveform, dict):
            continue
        channel = str(waveform.get("channel", "?"))
        time_array = arrays.get(f"time_{channel}")
        value_array = arrays.get(f"value_{channel}")
        if time_array is None or value_array is None:
            continue
        unit = str(waveform.get("value_unit", ""))
        series.append(prepare_series(time_array, value_array, channel))
        stats = waveform.get("stats")
        if isinstance(stats, dict):
            lines.append(
                f"{escape(channel)}: n={stats.get('sample_count', '?')}, "
                f"min={stats.get('min', '?')} {escape(unit)}, max={stats.get('max', '?')} {escape(unit)}, "
                f"p-p={stats.get('peak_to_peak', '?')} {escape(unit)}, rms={stats.get('rms', '?')} {escape(unit)}"
            )
    errors = metadata.get("errors")
    if errors:
        lines.append(f"Errors: {escape(str(errors))}")
    if not series:
        return _PreparedView(summary="\n".join(lines), series=None)
    first = next((w for w in metadata.get("waveforms", []) if isinstance(w, dict)), {})
    return _PreparedView(
        summary="\n".join(lines),
        series=series,
        title=path.name,
        xlabel=f"Time ({first.get('time_unit', '')})",
        ylabel=f"Signal ({first.get('value_unit', '')})",
    )


def _prepare_view(path: Path) -> _PreparedView:
    """Load and prepare a recording in a worker thread."""
    try:
        recording = load_recording(path)
    except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]",
            series=None,
        )

    sidecar = recording.metadata
    if sidecar.get("kind") == "scope-acquisition":
        return _prepare_scope_view(path, recording.arrays, sidecar)

    x_values = recording.arrays.get("x_values")
    if x_values is None:
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Missing x_values in recording.[/red]",
            series=None,
        )

    lines = [f"[b]{escape(path.name)}[/b]", f"{len(x_values)} point(s)"]
    run_metadata = sidecar.get("run_metadata")
    if isinstance(run_metadata, dict):
        lines.append("")
        lines.extend(f"{escape(str(k))}: {escape(str(v))}" for k, v in run_metadata.items())
    return _PreparedView(summary="\n".join(lines), series=None, recording=recording)


def _is_frequency_run(points: Sequence[dict]) -> bool:
    return any(
        "wavemeter_channel" in point or "measured_frequency_thz" in point for point in points
    )


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if np.isfinite(number) else None


def _format_value(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _sweep_data(
    recording: Recording, statistic: str
) -> tuple[
    bool,
    list[float],
    list[float],
    list[float | None],
    list[str],
    list[float | None],
]:
    arrays = recording.arrays
    x_values = np.asarray(arrays.get("x_values", []), dtype=np.float64)
    points = recording.metadata.get("points", [])
    if not isinstance(points, list):
        points = []
    points = [point if isinstance(point, dict) else {} for point in points]
    rows = len(x_values)
    labels = [
        str(points[index].get("label") or f"pt {index}") if index < len(points) else f"pt {index}"
        for index in range(rows)
    ]
    squeezing = arrays.get("trace_squeezing")
    shot_noise = arrays.get("trace_shot_noise")
    values = difference_values_many(
        squeezing if squeezing is not None else [None] * rows,
        shot_noise if shot_noise is not None else [None] * rows,
        statistic,
    )
    frequency = _is_frequency_run(points)
    measured = [
        _finite(points[index].get("measured_frequency_thz"))
        if frequency and index < len(points)
        else None
        for index in range(rows)
    ]
    plot_x = [
        measured[index] if measured[index] is not None else float(x_values[index])
        for index in range(rows)
    ]
    requested = [float(value) for value in x_values]
    return frequency, plot_x, requested, measured, labels, values


class TracesScreen(Page):
    """Browse runs, inspect a sweep, and drill into individual points."""

    def compose(self) -> ComposeResult:
        yield Static("Traces", classes="panel-title")
        yield Static("", id="traces-hint", classes="hint")
        yield Horizontal(
            ListView(id="traces-list"),
            VerticalScroll(
                Static("Select a run to preview it.", id="traces-summary"),
                Horizontal(
                    Static("Statistic", classes="hint"),
                    Select(_STATISTICS, value="mean", allow_blank=False, id="traces-statistic"),
                    id="traces-options",
                ),
                PlotextPlot(id="traces-plot"),
                DataTable(cursor_type="row", zebra_stripes=True, id="traces-points"),
                Static("", id="traces-point-summary"),
                PlotextPlot(id="traces-point-plot"),
                id="traces-detail",
            ),
            id="traces-body",
        )

    def on_mount(self) -> None:
        self._paths: list[Path] = []
        self._path: Path | None = None
        self._recording: Recording | None = None
        self._selected_point = 0
        self._render_generation = 0
        self._point_render_generation = 0
        self._suppress_events = False
        self.refresh_runs()

    def on_show(self) -> None:
        self.refresh_runs()

    def refresh_runs(self) -> None:
        """Re-scan the data directory while keeping the current selection.

        ListView can emit Highlighted while its children are rebuilt, so the
        one explicit selection below is the only load/render request we trust.
        """
        list_view = self.query_one("#traces-list", ListView)
        index = list_view.index
        previous = (
            self._paths[index] if index is not None and 0 <= index < len(self._paths) else None
        )
        self._paths = sorted(
            _DATA_ROOT.glob("**/*.npz"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        self.query_one("#traces-hint", Static).update(
            f"Runs are read from {escape(str(_DATA_ROOT))}"
        )
        self._suppress_events = True
        try:
            list_view.clear()
            for path in self._paths:
                list_view.append(ListItem(Label(_run_label(path, path.stat().st_mtime))))
            if self._paths:
                list_view.index = self._paths.index(previous) if previous in self._paths else 0
        finally:
            self._suppress_events = False

        if self._paths:
            # Select explicitly after rebuilding; child mounting can emit its
            # own highlight event before the list is fully populated.
            self._select(self._paths[list_view.index or 0])
        else:
            self._path = None
            self._recording = None
            self._show_empty()

    def _set_sweep_visible(self, visible: bool) -> None:
        for widget_id in (
            "#traces-statistic",
            "#traces-options",
            "#traces-points",
            "#traces-point-summary",
            "#traces-point-plot",
        ):
            self.query_one(widget_id).display = visible

    def _show_empty(self) -> None:
        self._set_sweep_visible(False)
        self.query_one("#traces-summary", Static).update(
            "No recordings yet.\n\nSweep and Scope acquisitions are saved automatically "
            "and will appear here."
        )
        for widget_id in ("#traces-plot", "#traces-point-plot"):
            plot = self.query_one(widget_id, PlotextPlot)
            plot.plt.clear_data()
            plot.refresh()
        self.query_one("#traces-point-summary", Static).update("")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Follow Up/Down directly; Enter/click still select the same row."""
        if self._suppress_events or event.list_view.id != "traces-list":
            return
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._paths):
            self._select(self._paths[index])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "traces-list":
            return
        if 0 <= event.index < len(self._paths):
            self._select(self._paths[event.index])

    def _select(self, path: Path) -> None:
        if path == self._path:
            # A ListView rebuild can report the same highlight more than once;
            # immutable files do not need another load.
            return
        self._path = path
        self._recording = None
        self._render_generation += 1
        generation = self._render_generation
        self._load_and_prepare(generation, path)

    @work(thread=True, exclusive=True, group="traces-load", exit_on_error=False)
    def _load_and_prepare(self, generation: int, path: Path) -> None:
        prepared = _prepare_view(path)
        self._ui(self._apply_view, generation, path, prepared)

    def _apply_view(self, generation: int, path: Path, prepared: _PreparedView) -> None:
        if generation != self._render_generation:
            # A slower load for an older selection must never replace a newer one.
            return
        self.query_one("#traces-summary", Static).update(prepared.summary)
        if prepared.recording is not None:
            self._recording = prepared.recording
            self._selected_point = 0
            self._set_sweep_visible(True)
            self._render_sweep()
            return

        self._recording = None
        self._set_sweep_visible(False)
        plot = self.query_one("#traces-plot", PlotextPlot)
        if prepared.series is None:
            plot.plt.clear_data()
            plot.refresh()
        else:
            draw_series(
                plot,
                prepared.series,
                title=prepared.title,
                xlabel=prepared.xlabel,
                ylabel=prepared.ylabel,
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "traces-statistic" and self._recording is not None:
            self._render_sweep()

    def _render_sweep(self) -> None:
        recording = self._recording
        if recording is None:
            return
        statistic = str(self.query_one("#traces-statistic", Select).value)
        frequency, plot_x, requested, measured, labels, values = _sweep_data(recording, statistic)

        valid = [
            (plot_x[index], value)
            for index, value in enumerate(values)
            if value is not None and np.isfinite(plot_x[index])
        ]
        plot = self.query_one("#traces-plot", PlotextPlot)
        draw_series(
            plot,
            [([x for x, _ in valid], [value for _, value in valid], None)] if valid else [],
            title=recording.path.name,
            xlabel="Frequency (THz)" if frequency else "RBW (Hz)",
            ylabel=f"{'Mean' if statistic == 'mean' else 'Minimum'} delta",
        )

        table = self.query_one("#traces-points", DataTable)
        selected = min(self._selected_point, max(len(requested) - 1, 0))
        self._suppress_events = True
        try:
            table.clear(columns=True)
            if frequency:
                table.add_columns("Point", "Requested (THz)", "Measured (THz)", "delta")
                rows = [
                    (
                        str(index),
                        _format_value(requested[index], 9),
                        _format_value(measured[index], 9),
                        _format_value(values[index]),
                    )
                    for index in range(len(requested))
                ]
            else:
                table.add_columns("Point", "RBW (Hz)", "delta")
                rows = [
                    (
                        str(index),
                        _format_value(requested[index]),
                        _format_value(values[index]),
                    )
                    for index in range(len(requested))
                ]
            table.add_rows(rows)
            if rows:
                table.move_cursor(row=selected, column=0)
        finally:
            self._suppress_events = False
        self._selected_point = selected
        self._render_point(labels, requested, measured, values, frequency)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suppress_events or event.data_table.id != "traces-points":
            return
        self._selected_point = event.cursor_row
        self._render_current_point()

    def _render_current_point(self) -> None:
        recording = self._recording
        if recording is None:
            return
        frequency, _, requested, measured, labels, values = _sweep_data(
            recording, str(self.query_one("#traces-statistic", Select).value)
        )
        self._render_point(labels, requested, measured, values, frequency)

    def _render_point(
        self,
        labels: Sequence[str],
        requested: Sequence[float],
        measured: Sequence[float | None],
        values: Sequence[float | None],
        frequency: bool,
    ) -> None:
        recording = self._recording
        summary = self.query_one("#traces-point-summary", Static)
        if recording is None or not labels:
            summary.update("")
            return
        index = min(self._selected_point, len(labels) - 1)
        request = requested[index]
        actual = measured[index] if index < len(measured) else None
        metric = values[index] if index < len(values) else None
        lines = [f"[b]Point {index}[/b] · {escape(labels[index])}"]
        if frequency:
            lines.append(f"Requested: {_format_value(request, 9)} THz")
            lines.append(f"Measured: {_format_value(actual, 9)} THz")
        else:
            lines.append(f"RBW: {_format_value(request)} Hz")
        statistic = str(self.query_one("#traces-statistic", Select).value)
        lines.append(
            f"{'Mean' if statistic == 'mean' else 'Minimum'} delta: {_format_value(metric)}"
        )
        summary.update("\n".join(lines))

        arrays = recording.arrays
        rows_for_worker: list[tuple[str, np.ndarray]] = []
        for name in ("squeezing", "shot_noise"):
            rows = arrays.get(f"trace_{name}")
            if rows is None or rows.ndim != 2 or index >= rows.shape[0]:
                continue
            row = rows[index]
            if np.all(np.isnan(row)):
                continue
            rows_for_worker.append((name, row))

        self._point_render_generation += 1
        generation = self._point_render_generation
        self._prepare_point_plot(
            generation,
            recording.path,
            index,
            rows_for_worker,
        )

    @work(thread=True, exclusive=True, group="traces-point-render", exit_on_error=False)
    def _prepare_point_plot(
        self,
        generation: int,
        path: Path,
        index: int,
        rows: list[tuple[str, np.ndarray]],
    ) -> None:
        series = [prepare_series(np.arange(len(row)), row, name) for name, row in rows]
        self._ui(self._apply_point_plot, generation, path, index, series)

    def _apply_point_plot(
        self,
        generation: int,
        path: Path,
        index: int,
        series: list[tuple[list[float], list[float], str | None]],
    ) -> None:
        if generation != self._point_render_generation:
            return
        recording = self._recording
        if recording is None or recording.path != path or self._selected_point != index:
            return
        plot = self.query_one("#traces-point-plot", PlotextPlot)
        draw_series(
            plot,
            series,
            title=f"{recording.path.name} · point {index}",
            xlabel="Trace sample",
            ylabel="Signal",
        )
