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
from datetime import datetime
from pathlib import Path

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

from ...analysis import (
    STATISTICS,
    ScopeSummary,
    Statistic,
    SweepPoint,
    SweepSummary,
    summarize_scope,
    summarize_sweep,
)
from ...experiment.io import DATA_ROOT, STEM_PATTERN, Recording, load_recording
from ...waveform_math import (
    Trace,
    save_waveform_figure,
    scale_trace,
    subtract_background,
    subtract_traces,
    traces_from_scope_recording,
)
from ..commands import Command, CommandError
from ..plotting import describe_timebase, draw_series, format_si, prepare_series, time_series
from .page import FieldError, Page, _field, _finite_float

_DATA_ROOT = DATA_ROOT

# What the Select offers, and the one name each statistic carries everywhere it is
# shown (plot axis, table column, point summary). "Relative noise" is squeezing minus
# shot noise in dB; see the vocabulary in iyzee.analysis.
_STATISTICS = (
    ("Mean relative noise", "mean"),
    ("Minimum relative noise", "minimum"),
)
_STATISTIC_NAMES = {value: label for label, value in _STATISTICS}

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


def _timebase_lines(summary: ScopeSummary) -> list[str]:
    """Horizontal setup of a recorded scope run: time/div, window, sample interval and rate."""
    lines: list[str] = []
    if summary.time_per_div is not None:
        lines.append(f"Time/div: {format_si(summary.time_per_div, 's')}")
    for timebase in summary.timebases:
        prefix = f"{', '.join(timebase.channels)}: " if len(summary.timebases) > 1 else ""
        first, *rest = describe_timebase(
            timebase.offset, timebase.interval, timebase.samples, timebase.unit
        )
        lines.append(escape(prefix + first))
        lines.extend(escape(line) for line in rest)
    return lines


def _unknown(value: float | int | None) -> str:
    return "?" if value is None else str(value)


def _scope_summary(path: Path, recording: Recording, traces: Sequence[Trace]) -> str:
    summary = summarize_scope(recording, traces)
    lines = [f"[b]{escape(path.name)}[/b]", "Scope acquisition"]
    if summary.measurement_id:
        lines.append(f"Measurement: {escape(summary.measurement_id)}")
    if summary.started_at_utc:
        lines.append(f"Started: {escape(summary.started_at_utc)}")
    if summary.instrument_address is not None:
        lines.append("Instrument: " + escape(summary.instrument_address or "address unknown"))
    for stats in summary.channels:
        unit = escape(stats.value_unit)
        lines.append(
            f"{escape(stats.channel)}: n={_unknown(stats.sample_count)}, "
            f"min={_unknown(stats.minimum)} {unit}, "
            f"max={_unknown(stats.maximum)} {unit}, "
            f"p-p={_unknown(stats.peak_to_peak)} {unit}, "
            f"rms={_unknown(stats.rms)} {unit}"
        )
    lines.extend(_timebase_lines(summary))
    if summary.errors:
        lines.append(f"Errors: {escape(str(summary.errors))}")
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


def _as_statistic(value: str) -> Statistic:
    """Narrow the Select's text to a known statistic; the Select only offers known ones."""
    for statistic in STATISTICS:
        if statistic == value:
            return statistic
    raise ValueError(f"unknown statistic {value!r}")


def _format_value(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _format_db(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f} dB"


# Width of the run list as a percent of the page; the detail pane takes the rest.
LIST_WIDTHS = (20, 25, 30, 35, 40, 45, 50, 55, 60)
DEFAULT_LIST_WIDTH = 35


def run_label(path: Path, mtime: float) -> str:
    """List label for one saved run: its name (if any) and save time."""
    when = datetime.fromtimestamp(mtime).astimezone()
    match = STEM_PATTERN.match(path.stem)
    name = match["name"] if match else None
    prefix = f"{escape(name)}  " if name else ""
    return f"{prefix}{when:%Y-%m-%d %H:%M:%S}"


class ResultsScreen(Page):
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

    BINDINGS = [
        Binding("left_square_bracket", "list_narrower", "List narrower"),
        Binding("right_square_bracket", "list_wider", "List wider"),
    ]

    def on_mount(self) -> None:
        self._list_width = self.iyzee_app.prefs.get("results_list_width", DEFAULT_LIST_WIDTH)
        self._set_list_width(self._list_width)
        self._paths: list[Path] = []
        self._path: Path | None = None
        self._suppress_events = False
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

    def on_show(self) -> None:
        self.refresh_runs()
        # Focus inside the page, so its keys (list resize, list navigation) work at once.
        self.query_one("#results-list").focus()

    def refresh_runs(self) -> None:
        """Re-scan the data directory (newest first), keeping the selection.

        ``ListView`` posts its own ``Highlighted`` message on every change to
        ``.index``, including the implicit ``None -> 0`` it makes the moment
        the first item is mounted into a previously-empty list, not just the
        explicit assignment below. Rebuilding the list would otherwise drive
        :meth:`on_list_view_highlighted` (and everything it triggers: a file
        load, a worker-threaded render) two or three times for what is, to
        the person looking at the screen, one visit to this page.
        ``_suppress_events`` turns those off for the rebuild, and this method
        makes the one call that matters, to ``_select`` or ``_show_empty``,
        itself.
        """
        list_view = self.query_one(f"#{self.LIST_ID}", ListView)
        index = list_view.index
        previous = (
            self._paths[index] if index is not None and 0 <= index < len(self._paths) else None
        )
        self._paths = sorted(
            _DATA_ROOT.glob("**/*.npz"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        self.query_one(f"#{self.HINT_ID}", Static).update(
            f"Saved recordings are read from {escape(str(_DATA_ROOT))}"
        )
        self._suppress_events = True
        try:
            list_view.clear()
            for path in self._paths:
                list_view.append(ListItem(Label(run_label(path, path.stat().st_mtime))))
            if self._paths:
                list_view.index = self._paths.index(previous) if previous in self._paths else 0
        finally:
            self._suppress_events = False

        if self._paths:
            self._select(self._paths[list_view.index or 0])
        else:
            self._show_empty()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Follow Up/Down directly."""
        if self._suppress_events or event.list_view.id != self.LIST_ID:
            return
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._paths):
            self._select(self._paths[index])

    def _set_list_width(self, percent: int) -> None:
        if percent not in LIST_WIDTHS:
            percent = DEFAULT_LIST_WIDTH
        self._list_width = percent
        run_list = self.query_one("#results-list")
        for width in LIST_WIDTHS:
            run_list.set_class(width == percent, f"w-{width}")

    def _resize_list(self, step: int) -> None:
        index = LIST_WIDTHS.index(self._list_width) + step
        percent = LIST_WIDTHS[max(0, min(index, len(LIST_WIDTHS) - 1))]
        if percent != self._list_width:
            self._set_list_width(percent)
            self.iyzee_app.prefs.set("results_list_width", percent)

    def action_list_narrower(self) -> None:
        self._resize_list(-1)

    def action_list_wider(self) -> None:
        self._resize_list(+1)

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

    def _sweep_summary(self) -> SweepSummary | None:
        recording = self._recording
        if recording is None:
            return None
        statistic = str(self.query_one("#results-statistic", Select).value)
        return summarize_sweep(recording, _as_statistic(statistic))

    def _render_sweep(self) -> None:
        recording = self._recording
        sweep = self._sweep_summary()
        if recording is None or sweep is None:
            return
        frequency = sweep.axis == "frequency"
        statistic_name = _STATISTIC_NAMES[sweep.statistic]

        valid = [
            (point.x, point.relative_noise_db)
            for point in sweep.points
            if point.relative_noise_db is not None and np.isfinite(point.x)
        ]
        plot = self.query_one("#results-plot", PlotextPlot)
        draw_series(
            plot,
            [([x for x, _ in valid], [value for _, value in valid], None)] if valid else [],
            title=recording.path.name,
            xlabel="Frequency (THz)" if frequency else "RBW (Hz)",
            ylabel=f"{statistic_name} (dB)",
        )

        table = self.query_one("#results-points", DataTable)
        selected = min(self._selected_point, max(len(sweep.points) - 1, 0))
        self._suppress_events = True
        try:
            table.clear(columns=True)
            rows: list[tuple[str, ...]]
            if frequency:
                table.add_columns(
                    "Point", "Requested (THz)", "Measured (THz)", "Relative noise (dB)"
                )
                rows = [
                    (
                        str(point.index),
                        _format_value(point.requested, 9),
                        _format_value(point.measured, 9),
                        _format_value(point.relative_noise_db),
                    )
                    for point in sweep.points
                ]
            else:
                table.add_columns("Point", "RBW (Hz)", "Relative noise (dB)")
                rows = [
                    (
                        str(point.index),
                        _format_value(point.requested),
                        _format_value(point.relative_noise_db),
                    )
                    for point in sweep.points
                ]
            table.add_rows(rows)
            if rows:
                table.move_cursor(row=selected, column=0)
        finally:
            self._suppress_events = False

        self._selected_point = selected
        self._render_point(sweep)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._suppress_events or event.data_table.id != "results-points":
            return
        self._selected_point = event.cursor_row
        self._render_current_point()

    def _render_current_point(self) -> None:
        sweep = self._sweep_summary()
        if sweep is not None:
            self._render_point(sweep)

    def _render_point(self, sweep: SweepSummary) -> None:
        recording = self._recording
        summary = self.query_one("#results-point-summary", Static)
        if recording is None or not sweep.points:
            summary.update("")
            return
        point: SweepPoint = sweep.points[min(self._selected_point, len(sweep.points) - 1)]
        index = point.index
        lines = [f"[b]Point {index}[/b] · {escape(point.label)}"]
        if sweep.axis == "frequency":
            lines.append(f"Requested: {_format_value(point.requested, 9)} THz")
            lines.append(f"Measured: {_format_value(point.measured, 9)} THz")
        else:
            lines.append(f"RBW: {_format_value(point.requested)} Hz")
        lines.append(f"{_STATISTIC_NAMES[sweep.statistic]}: {_format_db(point.relative_noise_db)}")
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
        series, xlabel = (
            time_series([(t.time, t.values, t.label) for t in traces], traces[0].time_unit)
            if traces
            else ([], None)
        )
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

    def commands(self) -> list[Command]:
        def width(args: list[str]) -> str:
            if len(args) != 1 or not args[0].isdigit():
                raise CommandError(f"usage: :width {LIST_WIDTHS[0]}-{LIST_WIDTHS[-1]} (percent)")
            percent = min(LIST_WIDTHS, key=lambda w: abs(w - int(args[0])))
            self._set_list_width(percent)
            self.iyzee_app.prefs.set("results_list_width", percent)
            return f"list width {percent}%"

        def latest(args: list[str]) -> str:
            run_list = self.query_one("#results-list", ListView)
            if not len(run_list):
                raise CommandError("no recordings found")
            run_list.index = 0
            return "newest recording selected"

        return [
            Command("refresh", lambda _: self.refresh_runs(), "Re-scan the data directory"),
            Command("latest", latest, "Select the newest recording"),
            Command("width", width, "Set the run list width (percent)", usage="<20-60>"),
        ]

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
