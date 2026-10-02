"""Results screen: browse saved sweep and scope recordings.

One recording browser handles both result types. Sweep recordings keep their
point-by-point inspection; scope acquisitions expose channel selection,
waveform operations, and PNG export.

File loading and waveform preview preparation stay off the UI thread. The
math used for scope operations remains in iyzee.waveform_math so the same
operations are available from scripts and the IPython console without any
Textual dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.widgets import Button, Checkbox, DataTable, Input, ListView, RichLog, Select, Static
from textual_plotext import PlotextPlot

from ...experiment import difference_values_many
from ...experiment.io import DATA_ROOT, Recording, load_recording
from ...waveform_math import (
    Trace,
    save_waveform_figure,
    scale_trace,
    subtract_background,
    subtract_traces,
    traces_from_scope_recording,
)
from ..plotting import draw_series, prepare_series
from .page import FieldError, _field, _finite_float
from .runlist import RunListPage

_DATA_ROOT = DATA_ROOT

_STATISTICS = (
    ("Mean delta", "mean"),
    ("Minimum delta", "minimum"),
)

_OPERATIONS = [
    ("Subtract: A - B", "subtract"),
    ("Background: mean over a region", "background-region"),
    ("Background: reference trace", "background-reference"),
    ("Scale axes", "scale"),
]

_OPERATION_FIELDS = {
    "subtract": ("chan-a", "chan-b"),
    "background-region": ("chan-a", "region-lo", "region-hi"),
    "background-reference": ("chan-a", "chan-b"),
    "scale": ("chan-a", "xscale", "xoffset", "yscale", "yoffset"),
}


@dataclass(frozen=True)
class _PreparedView:
    """Loaded recording plus the metadata needed for its initial view."""

    summary: str
    recording: Recording | None = None
    scope_traces: tuple[Trace, ...] = ()


def _scope_summary(path: Path, recording: Recording, traces: Sequence[Trace]) -> str:
    metadata = recording.metadata
    lines = [f"[b]{escape(path.name)}[/b]", "Scope acquisition"]
    if metadata.get("measurement_id"):
        lines.append(f"Measurement: {escape(str(metadata['measurement_id']))}")
    if metadata.get("started_at_utc"):
        lines.append(f"Started: {escape(str(metadata['started_at_utc']))}")
    instrument = metadata.get("instrument")
    if isinstance(instrument, dict):
        lines.append("Instrument: " + escape(str(instrument.get("address") or "address unknown")))

    waveforms = metadata.get("waveforms", [])
    if not isinstance(waveforms, list):
        waveforms = []
    stats_by_channel = {
        str(waveform.get("channel")): waveform.get("stats")
        for waveform in waveforms
        if isinstance(waveform, dict)
    }
    for trace in traces:
        stats = stats_by_channel.get(trace.label)
        if isinstance(stats, dict):
            lines.append(
                f"{escape(trace.label)}: n={stats.get('sample_count', '?')}, "
                f"min={stats.get('min', '?')} {escape(trace.value_unit)}, "
                f"max={stats.get('max', '?')} {escape(trace.value_unit)}, "
                f"p-p={stats.get('peak_to_peak', '?')} {escape(trace.value_unit)}, "
                f"rms={stats.get('rms', '?')} {escape(trace.value_unit)}"
            )
    errors = metadata.get("errors")
    if errors:
        lines.append(f"Errors: {escape(str(errors))}")
    if not traces:
        lines.append("\n[yellow]No channel data in this recording.[/yellow]")
    return "\n".join(lines)


def _prepare_view(path: Path) -> _PreparedView:
    """Load one recording in a worker and classify it by its durable schema."""
    try:
        recording = load_recording(path)
    except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]"
        )

    if recording.metadata.get("kind") == "scope-acquisition":
        traces = tuple(traces_from_scope_recording(recording))
        return _PreparedView(
            summary=_scope_summary(path, recording, traces),
            recording=recording,
            scope_traces=traces,
        )

    x_values = recording.arrays.get("x_values")
    if x_values is None:
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Missing x_values in recording.[/red]"
        )

    run_metadata = recording.metadata.get("run_metadata")
    lines = [f"[b]{escape(path.name)}[/b]", f"{len(x_values)} point(s)"]
    if isinstance(run_metadata, dict):
        lines.append("")
        lines.extend(f"{escape(str(k))}: {escape(str(v))}" for k, v in run_metadata.items())
    return _PreparedView(summary="\n".join(lines), recording=recording)


def _is_frequency_run(points: Sequence[dict]) -> bool:
    return any(
        "wavemeter_channel" in point or "measured_frequency_thz" in point for point in points
    )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if np.isfinite(number) else None


def _format_value(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


class _SweepData(NamedTuple):
    frequency: bool
    plot_x: list[float]
    requested: list[float]
    measured: list[float | None]
    labels: list[str]
    values: list[float | None]


def _sweep_data(recording: Recording, statistic: str) -> _SweepData:
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
    requested = [float(value) for value in x_values]
    plot_x = [
        measured_x if measured_x is not None else requested[index]
        for index, measured_x in enumerate(measured)
    ]
    return _SweepData(frequency, plot_x, requested, measured, labels, values)


class ResultsScreen(RunListPage):
    """Browse saved runs and inspect either sweep or scope results."""

    LIST_ID = "results-list"
    HINT_ID = "results-hint"

    def compose(self) -> ComposeResult:
        yield Static("Results", classes="panel-title")
        yield Static("", id="results-hint", classes="hint")
        yield Horizontal(
            ListView(id="results-list"),
            VerticalScroll(
                Static("Select a recording to inspect it.", id="results-summary"),
                Horizontal(
                    Static("Statistic", classes="hint"),
                    Select(_STATISTICS, value="mean", allow_blank=False, id="results-statistic"),
                    id="results-options",
                ),
                Grid(id="results-channels", classes="channel-toggle-grid"),
                Vertical(
                    Static("Combine or adjust waveforms", classes="panel-title"),
                    Static(
                        "Only the fields used by the chosen operation are applied.",
                        classes="hint",
                    ),
                    _field(
                        "Operation",
                        Select(_OPERATIONS, value="subtract", allow_blank=False, id="results-op"),
                    ),
                    Grid(
                        _field(
                            "Channel / trace A",
                            Select([], allow_blank=True, id="results-chan-a"),
                            id="results-field-chan-a",
                        ),
                        _field(
                            "Channel B / reference",
                            Select([], allow_blank=True, id="results-chan-b"),
                            id="results-field-chan-b",
                        ),
                        _field(
                            "Region start",
                            Input(value="0", id="results-region-lo"),
                            id="results-field-region-lo",
                        ),
                        _field(
                            "Region end",
                            Input(value="0", id="results-region-hi"),
                            id="results-field-region-hi",
                        ),
                        _field(
                            "X scale",
                            Input(value="1", id="results-xscale"),
                            id="results-field-xscale",
                        ),
                        _field(
                            "X offset",
                            Input(value="0", id="results-xoffset"),
                            id="results-field-xoffset",
                        ),
                        _field(
                            "Y scale",
                            Input(value="1", id="results-yscale"),
                            id="results-field-yscale",
                        ),
                        _field(
                            "Y offset",
                            Input(value="0", id="results-yoffset"),
                            id="results-field-yoffset",
                        ),
                        id="results-op-fields",
                        classes="field-grid",
                    ),
                    Horizontal(
                        Button("Apply operation", id="results-apply-op"),
                        Button("Clear derived traces", id="results-clear-op"),
                        id="results-op-buttons",
                    ),
                    id="results-op-panel",
                    classes="channel-panel",
                ),
                PlotextPlot(id="results-plot"),
                DataTable(cursor_type="row", zebra_stripes=True, id="results-points"),
                Static("", id="results-point-summary"),
                PlotextPlot(id="results-point-plot"),
                Horizontal(
                    Button("Export plot (PNG)", id="results-export", variant="success"),
                    id="results-export-row",
                ),
                RichLog(id="results-log", highlight=False, markup=True),
                id="results-detail",
            ),
            id="results-body",
        )

    def on_mount(self) -> None:
        self._init_run_list()
        self._recording: Recording | None = None
        self._measured: dict[str, Trace] = {}
        self._derived: dict[str, Trace] = {}
        self._checkboxes: dict[str, Checkbox] = {}
        self._selected_point = 0
        self._load_generation = 0
        self._render_generation = 0
        self._point_render_generation = 0
        self._set_sweep_visible(False)
        self._set_scope_visible(False)
        self._set_operation_fields("subtract")

    def _scan_runs(self) -> list[Path]:
        return list(_DATA_ROOT.glob("**/*.npz"))

    def _hint_text(self) -> str:
        return f"Saved recordings are read from {escape(str(_DATA_ROOT))}"

    def _set_sweep_visible(self, visible: bool) -> None:
        for widget_id in (
            "#results-statistic",
            "#results-options",
            "#results-points",
            "#results-point-summary",
            "#results-point-plot",
        ):
            self.query_one(widget_id).display = visible

    def _set_scope_visible(self, visible: bool) -> None:
        for widget_id in (
            "#results-channels",
            "#results-op-panel",
            "#results-export-row",
            "#results-log",
        ):
            self.query_one(widget_id).display = visible

    def _set_operation_fields(self, operation: str) -> None:
        active = set(_OPERATION_FIELDS.get(operation, ()))
        for name in (
            "chan-a",
            "chan-b",
            "region-lo",
            "region-hi",
            "xscale",
            "xoffset",
            "yscale",
            "yoffset",
        ):
            self.query_one(f"#results-field-{name}").display = name in active

    def _clear_preview(self) -> None:
        plot = self.query_one("#results-plot", PlotextPlot)
        plot.plt.clear_data()
        plot.refresh()
        point_plot = self.query_one("#results-point-plot", PlotextPlot)
        point_plot.plt.clear_data()
        point_plot.refresh()
        self.query_one("#results-point-summary", Static).update("")

    def _show_empty(self) -> None:
        self._path = None
        self._recording = None
        self._measured = {}
        self._derived = {}
        self._set_sweep_visible(False)
        self._set_scope_visible(False)
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._clear_preview()
        self.query_one("#results-summary", Static).update(
            "No recordings yet.\n\nSweep and Scope acquisitions are saved automatically "
            "and will appear here."
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != self.LIST_ID:
            return
        if 0 <= event.index < len(self._paths):
            self._select(self._paths[event.index])

    def _select(self, path: Path) -> None:
        if path == self._path:
            return
        self._path = path
        self._recording = None
        self._measured = {}
        self._derived = {}
        self._selected_point = 0
        self._load_generation += 1
        self._render_generation += 1
        self._point_render_generation += 1
        generation = self._load_generation

        self._set_sweep_visible(False)
        self._set_scope_visible(False)
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._clear_preview()
        self.query_one("#results-summary", Static).update(f"[b]{escape(path.name)}[/b]\n\nLoading…")
        self._load_selected(generation, path)

    @work(thread=True, exclusive=True, group="results-load", exit_on_error=False)
    def _load_selected(self, generation: int, path: Path) -> None:
        prepared = _prepare_view(path)
        self._ui(self._apply_view, generation, path, prepared)

    def _apply_view(self, generation: int, path: Path, prepared: _PreparedView) -> None:
        if generation != self._load_generation or path != self._path:
            return

        self.query_one("#results-summary", Static).update(prepared.summary)
        self._recording = prepared.recording
        self._set_sweep_visible(False)
        self._set_scope_visible(False)

        if prepared.recording is None:
            self._measured = {}
            self._derived = {}
            self._rebuild_channel_checkboxes()
            self._refresh_operand_choices()
            return

        if prepared.recording.metadata.get("kind") == "scope-acquisition":
            self._measured = {trace.label: trace for trace in prepared.scope_traces}
            self._derived = {}
            self._rebuild_channel_checkboxes()
            self._refresh_operand_choices()
            self._set_scope_visible(True)
            self._set_operation_fields(str(self.query_one("#results-op", Select).value))
            self._redraw_scope()
            return

        self._measured = {}
        self._derived = {}
        self._set_sweep_visible(True)
        self._render_sweep()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "results-statistic" and self._recording is not None:
            if self._recording.metadata.get("kind") != "scope-acquisition":
                self._render_sweep()
        elif event.select.id == "results-op":
            self._set_operation_fields(str(event.select.value))

    def _render_sweep(self) -> None:
        recording = self._recording
        if recording is None:
            return
        statistic = str(self.query_one("#results-statistic", Select).value)
        frequency, plot_x, requested, measured, labels, values = _sweep_data(recording, statistic)

        valid = [
            (plot_x[index], value)
            for index, value in enumerate(values)
            if value is not None and np.isfinite(plot_x[index])
        ]
        plot = self.query_one("#results-plot", PlotextPlot)
        draw_series(
            plot,
            [([x for x, _ in valid], [value for _, value in valid], None)] if valid else [],
            title=recording.path.name,
            xlabel="Frequency (THz)" if frequency else "RBW (Hz)",
            ylabel=f"{'Mean' if statistic == 'mean' else 'Minimum'} delta",
        )

        table = self.query_one("#results-points", DataTable)
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
        if self._suppress_events or event.data_table.id != "results-points":
            return
        self._selected_point = event.cursor_row
        self._render_current_point()

    def _render_current_point(self) -> None:
        recording = self._recording
        if recording is None:
            return
        frequency, _, requested, measured, labels, values = _sweep_data(
            recording, str(self.query_one("#results-statistic", Select).value)
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
        summary = self.query_one("#results-point-summary", Static)
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
        statistic = str(self.query_one("#results-statistic", Select).value)
        lines.append(
            f"{'Mean' if statistic == 'mean' else 'Minimum'} delta: {_format_value(metric)}"
        )
        summary.update("\n".join(lines))

        rows_for_worker: list[tuple[str, np.ndarray]] = []
        for name in ("squeezing", "shot_noise"):
            rows = recording.arrays.get(f"trace_{name}")
            if rows is None or rows.ndim != 2 or index >= rows.shape[0]:
                continue
            row = rows[index]
            if np.all(np.isnan(row)):
                continue
            rows_for_worker.append((name, row))

        self._point_render_generation += 1
        generation = self._point_render_generation
        self._prepare_point_plot(generation, recording.path, index, rows_for_worker)

    @work(thread=True, exclusive=True, group="results-point-render", exit_on_error=False)
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
        draw_series(
            self.query_one("#results-point-plot", PlotextPlot),
            series,
            title=f"{recording.path.name} · point {index}",
            xlabel="Trace sample",
            ylabel="Signal",
        )

    def _all_traces(self) -> dict[str, Trace]:
        return {**self._measured, **self._derived}

    def _rebuild_channel_checkboxes(self) -> None:
        grid = self.query_one("#results-channels", Grid)
        grid.remove_children()
        self._checkboxes = {}
        self._suppress_events = True
        try:
            for label in self._all_traces():
                checkbox = Checkbox(label, value=True, classes="results-chan-toggle")
                self._checkboxes[label] = checkbox
                grid.mount(checkbox)
        finally:
            self._suppress_events = False

    def _refresh_operand_choices(self) -> None:
        labels = sorted(self._all_traces())
        options = [(label, label) for label in labels]
        self.query_one("#results-chan-a", Select).set_options(options)
        self.query_one("#results-chan-b", Select).set_options(options)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not self._suppress_events and "results-chan-toggle" in event.checkbox.classes:
            self._redraw_scope()

    def _selected_traces(self) -> list[Trace]:
        all_traces = self._all_traces()
        return [
            all_traces[label]
            for label, checkbox in self._checkboxes.items()
            if checkbox.value and label in all_traces
        ]

    def _redraw_scope(self) -> None:
        if self._recording is None or self._recording.metadata.get("kind") != "scope-acquisition":
            return
        self._render_generation += 1
        generation = self._render_generation
        traces = self._selected_traces()
        title = self._path.name if self._path else None
        self._render_scope(generation, traces, title)

    @work(thread=True, exclusive=True, group="results-render", exit_on_error=False)
    def _render_scope(self, generation: int, traces: list[Trace], title: str | None) -> None:
        series = [prepare_series(trace.time, trace.values, trace.label) for trace in traces]
        xlabel = f"Time ({traces[0].time_unit})" if traces else None
        ylabel = f"Signal ({traces[0].value_unit})" if traces else None
        self._ui(self._apply_scope_preview, generation, series, title, xlabel, ylabel)

    def _apply_scope_preview(
        self,
        generation: int,
        series: list[tuple[list[float], list[float], str | None]],
        title: str | None,
        xlabel: str | None,
        ylabel: str | None,
    ) -> None:
        if generation != self._render_generation:
            return
        draw_series(
            self.query_one("#results-plot", PlotextPlot),
            series,
            title=title,
            xlabel=xlabel,
            ylabel=ylabel,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "results-apply-op":
            self._apply_operation()
        elif event.button.id == "results-clear-op":
            self._clear_derived()
        elif event.button.id == "results-export":
            self._export()

    def _operand(self, field_id: str, label: str) -> Trace:
        name = self._selected(field_id, label)
        trace = self._all_traces().get(name)
        if trace is None:
            raise FieldError(field_id, f"{label}: {name!r} is not an available channel/trace")
        return trace

    def _apply_operation(self) -> None:
        log_widget = self.query_one("#results-log", RichLog)
        if not self._all_traces():
            self.notify("Select a scope acquisition with channel data first.", severity="error")
            return
        operation = self._selected("results-op", "Operation")
        try:
            if operation == "subtract":
                a = self._operand("results-chan-a", "Channel A")
                b = self._operand("results-chan-b", "Channel B")
                result = subtract_traces(a, b)
            elif operation == "background-region":
                a = self._operand("results-chan-a", "Channel A")
                lo = self._read("results-region-lo", _finite_float, "Region start")
                hi = self._read("results-region-hi", _finite_float, "Region end")
                result = subtract_background(a, region=(lo, hi))
            elif operation == "background-reference":
                a = self._operand("results-chan-a", "Channel A")
                reference = self._operand("results-chan-b", "Reference")
                result = subtract_background(a, reference=reference)
            else:
                a = self._operand("results-chan-a", "Channel A")
                x_scale = self._read("results-xscale", _finite_float, "X scale")
                x_offset = self._read("results-xoffset", _finite_float, "X offset")
                y_scale = self._read("results-yscale", _finite_float, "Y scale")
                y_offset = self._read("results-yoffset", _finite_float, "Y offset")
                result = scale_trace(
                    a, x_scale=x_scale, x_offset=x_offset, y_scale=y_scale, y_offset=y_offset
                )
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(str(exc), severity="error", markup=False)
            return
        except ValueError as exc:
            self.notify(str(exc), severity="error", markup=False)
            return

        self._derived[result.label] = result
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw_scope()
        log_widget.write(f"Added derived trace: {escape(result.label)}")

    def _clear_derived(self) -> None:
        self._derived = {}
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw_scope()
        self.query_one("#results-log", RichLog).write("Cleared derived traces.")

    def _export(self) -> None:
        if self._path is None:
            self.notify("Select a scope acquisition first.", severity="error")
            return
        if self._recording is None or self._recording.metadata.get("kind") != "scope-acquisition":
            self.notify("Select a scope acquisition first.", severity="error")
            return
        traces = self._selected_traces()
        if not traces:
            self.notify("Select at least one channel/trace to export.", severity="error")
            return
        out_path = self._path.with_name(f"{self._path.stem}-plot.png")
        self.query_one("#results-export", Button).disabled = True
        self._save_export(traces, out_path, self._path.name)

    @work(thread=True, exclusive=True, group="results-export", exit_on_error=False)
    def _save_export(self, traces: list[Trace], out_path: Path, title: str) -> None:
        error: Exception | None = None
        try:
            save_waveform_figure(traces, out_path, title=title)
        except Exception as exc:  # noqa: BLE001 - reported, not raised, from a UI action
            error = exc
        self._ui(self._finish_export, out_path, error)

    def _finish_export(self, out_path: Path, error: Exception | None) -> None:
        self.query_one("#results-export", Button).disabled = False
        log_widget = self.query_one("#results-log", RichLog)
        if error is not None:
            log_widget.write(f"[red]Export failed: {escape(str(error))}[/red]")
            self.notify(f"Export failed: {error}", severity="error", markup=False)
            return
        log_widget.write(f"Saved plot: {escape(str(out_path))}")
        self.notify(f"Saved {out_path.name}")
