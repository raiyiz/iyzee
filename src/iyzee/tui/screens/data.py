"""Data screen: browse saved scope acquisitions, combine or adjust their
waveforms, and preview or export the result as a real matplotlib figure.

Reuses ``experiment.io.load_recording`` (the same loader Traces uses) rather
than re-parsing the ``.npz``/``.json`` pair, and does its own numeric work
entirely through ``iyzee.waveform_math`` — plain functions with no Textual
import, so the subtract/background-correct/scale operations this screen's
buttons trigger are identically usable from a script or the console. This
screen's job is picking channels, reading the math-op form into one of those
function calls, and drawing the result — not the math itself.

Unlike Sweep and Scope, nothing here talks to a socket, so there's no
hardware I/O to keep off the UI thread — but building a matplotlib figure
and extracting its line data is real, non-trivial CPU work (roughly half a
second for a 1M-sample waveform, measured; a long-memory scope acquisition
can be many times that), and that *does* block Textual's single-threaded UI
loop just as effectively as a blocking socket call would. So
:func:`~iyzee.waveform_math.build_waveform_figure`/``figure_series`` (in
``_render_waveforms``) and the export's ``savefig`` (in ``_export``) run in
``@work(thread=True)`` workers, same as Sweep/Scope's hardware calls; only
the actual math-op application and the final ``draw_series`` widget update
stay synchronous, since matplotlib figure objects are plain Python objects
safe to build off the main thread (see ``tui.__init__`` for why the backend
must be non-interactive for that to hold), but only the main thread may ever
touch a Textual widget.

A checkbox toggle can be superseded by another before its worker finishes
(the user keeps clicking while a big waveform is still rendering); each
redraw request carries a generation number, checked before the result is
applied, so a slow, now-stale render can never land after a newer one —
the same "is this still the current thing" guard ``ScopeScreen._scope_is_
current`` uses for a different kind of staleness.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib.pyplot as plt
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

from ...experiment.io import DATA_ROOT, load_recording
from ...waveform_math import (
    Trace,
    build_waveform_figure,
    save_waveform_figure,
    scale_trace,
    subtract_background,
    subtract_traces,
    traces_from_scope_recording,
)
from ..plotting import draw_series, figure_series
from .page import FieldError, Page, _field, _finite_float
from .traces import _run_label

log = logging.getLogger("iyzee.tui")

# See traces._DATA_ROOT: kept as a separate name so it stays independently
# monkeypatchable in tests rather than reading experiment.io.DATA_ROOT directly.
_DATA_ROOT = DATA_ROOT

_OPERATIONS = [
    ("Subtract: A - B", "subtract"),
    ("Background: mean over a region", "background-region"),
    ("Background: reference trace", "background-reference"),
    ("Scale axes", "scale"),
]

# Which of the operand/parameter fields below actually matter for each
# operation. Every field stays visible and editable regardless — see
# "_apply_operation" — so switching operations never loses what's already
# typed into a field only some operations use.
_OPERAND_FIELDS = {
    "subtract": ("data-chan-a", "data-chan-b"),
    "background-region": ("data-chan-a", "data-region-lo", "data-region-hi"),
    "background-reference": ("data-chan-a", "data-chan-b"),
    "scale": ("data-chan-a", "data-xscale", "data-xoffset", "data-yscale", "data-yoffset"),
}


def _peek_kind(path: Path) -> str | None:
    """The sidecar's ``"kind"`` field, without loading the ``.npz`` arrays.

    Used only to filter the run list to scope acquisitions (the kind this
    screen's math operations apply to) before paying for a full
    ``load_recording()``; ``None`` on any error, same as ``load_recording``'s
    own tolerance for a missing/corrupt sidecar, just too early to have a
    ``Recording`` to hang the fallback off of yet.
    """
    try:
        return json.loads(path.with_suffix(".json").read_text()).get("kind")
    except OSError, ValueError, AttributeError:
        return None


class DataScreen(Page):
    """List scope acquisitions on the left; combine/adjust and preview on the right."""

    def compose(self) -> ComposeResult:
        yield Static("Data", classes="panel-title")
        yield Static("", id="data-hint", classes="hint")
        yield Horizontal(
            ListView(id="data-list"),
            VerticalScroll(
                Static("Select a scope acquisition to analyze it.", id="data-summary"),
                Grid(id="data-channels", classes="channel-toggle-grid"),
                Vertical(
                    Static("Combine or adjust waveforms", classes="panel-title"),
                    Static(
                        "Only the fields the chosen operation uses are read when you Apply.",
                        classes="hint",
                    ),
                    _field(
                        "Operation",
                        Select(_OPERATIONS, value="subtract", allow_blank=False, id="data-op"),
                    ),
                    Grid(
                        _field("Channel / trace A", Select([], allow_blank=True, id="data-chan-a")),
                        _field(
                            "Channel B / reference", Select([], allow_blank=True, id="data-chan-b")
                        ),
                        _field("Region start", Input(value="0", id="data-region-lo")),
                        _field("Region end", Input(value="0", id="data-region-hi")),
                        _field("X scale", Input(value="1", id="data-xscale")),
                        _field("X offset", Input(value="0", id="data-xoffset")),
                        _field("Y scale", Input(value="1", id="data-yscale")),
                        _field("Y offset", Input(value="0", id="data-yoffset")),
                        id="data-op-fields",
                        classes="field-grid",
                    ),
                    Horizontal(
                        Button("Apply operation", id="data-apply-op"),
                        Button("Clear derived traces", id="data-clear-op"),
                        id="data-op-buttons",
                    ),
                    id="data-op-panel",
                    classes="channel-panel",
                ),
                PlotextPlot(id="data-plot"),
                Horizontal(
                    Button("Export plot (PNG)", id="data-export", variant="success"),
                    id="data-export-row",
                ),
                RichLog(id="data-log", highlight=False, markup=True),
                id="data-detail",
            ),
            id="data-body",
        )

    def on_mount(self) -> None:
        self._paths: list[Path] = []
        self._path: Path | None = None
        self._measured: dict[str, Trace] = {}
        self._derived: dict[str, Trace] = {}
        self._checkboxes: dict[str, Checkbox] = {}
        self._suppress_events = False
        self._render_generation = 0
        self.refresh_runs()

    def on_show(self) -> None:
        self.refresh_runs()

    def _all_traces(self) -> dict[str, Trace]:
        """Measured channels plus derived traces, by label.

        A derived trace reusing an existing label (re-running the same
        operation, most often) intentionally replaces the earlier entry
        rather than being kept alongside it under a second name.
        """
        return {**self._measured, **self._derived}

    # -- run list --------------------------------------------------------------------------

    def refresh_runs(self) -> None:
        """Re-scan the data directory for scope acquisitions.

        Filtered to scope acquisitions (see ``_peek_kind``): a sweep
        checkpoint has no per-channel waveforms for the math operations here
        to act on, so listing it would only be a dead end for this screen —
        Traces already lists every recording, sweep and scope alike, for
        browsing.

        ``ListView`` posts its own ``Highlighted`` message on every change to
        ``.index`` — including the implicit ``None -> 0`` it makes itself the
        moment the first item is mounted into a previously-empty list, not
        just the explicit assignment below — so rebuilding the list here
        would otherwise drive ``on_list_view_highlighted`` (and everything it
        triggers: a file load, a worker-threaded render) two or three times
        over for what is, to the person looking at the screen, one visit to
        this page. ``_suppress_events`` turns those off for the rebuild, and
        this function makes the one call that actually matters — to
        ``_select``/``_show_empty`` — itself, once, explicitly, rather than
        leaving it to however many of ``ListView``'s own events happen to
        fire along the way.
        """
        list_view = self.query_one("#data-list", ListView)
        index = list_view.index
        previous = (
            self._paths[index] if index is not None and 0 <= index < len(self._paths) else None
        )

        self._paths = sorted(
            (p for p in _DATA_ROOT.glob("**/*.npz") if _peek_kind(p) == "scope-acquisition"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        self.query_one("#data-hint", Static).update(
            f"Scope acquisitions are read from {escape(str(_DATA_ROOT))}"
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
            self._select(self._paths[list_view.index or 0])
        else:
            self._show_empty()

    def _show_empty(self) -> None:
        self._path = None
        self._measured = {}
        self._derived = {}
        self.query_one("#data-summary", Static).update(
            "No scope acquisitions yet.\n\nAcquire waveforms from the Scope page and they "
            "will appear here."
        )
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._suppress_events or event.list_view.id != "data-list":
            return
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._paths):
            self._select(self._paths[index])

    # -- selecting a run ---------------------------------------------------------------------

    def _select(self, path: Path) -> None:
        if path == self._path:
            # Reached again for the run already showing — most often ListView
            # re-posting Highlighted for an index that didn't actually change
            # (it fires on the implicit None -> 0 a freshly-populated list
            # makes for itself, not just on a real explicit change), or
            # on_mount and on_show both scanning at once. Filenames are
            # timestamped and never rewritten under the same name, so "same
            # path" reliably means "nothing to redo" — skip re-reading the
            # file and re-rendering rather than trusting how many times
            # Textual happens to have called this for one visible selection.
            return
        summary = self.query_one("#data-summary", Static)
        try:
            recording = load_recording(path)
            measured = traces_from_scope_recording(recording)
        except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
            summary.update(
                f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]"
            )
            self._path = None
            self._measured = {}
            self._derived = {}
            self._rebuild_channel_checkboxes()
            self._refresh_operand_choices()
            self._redraw()
            return

        self._path = path
        self._measured = {trace.label: trace for trace in measured}
        self._derived = {}

        lines = [f"[b]{escape(path.name)}[/b]"]
        instrument = recording.metadata.get("instrument")
        if isinstance(instrument, dict) and instrument.get("identity"):
            lines.append(escape(str(instrument["identity"])))
        acquisition = recording.metadata.get("acquisition")
        if isinstance(acquisition, dict) and acquisition.get("warnings"):
            lines.append(f"[yellow]{escape('; '.join(acquisition['warnings']))}[/yellow]")
        if not measured:
            lines.append("\n[yellow]No channel data in this recording.[/yellow]")
        summary.update("\n".join(lines))

        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw()

    # -- channel checkboxes and operand choices -----------------------------------------------

    def _rebuild_channel_checkboxes(self) -> None:
        """Rebuild the channel/derived-trace checkboxes for the current selection.

        Mounting a ``Checkbox`` fires its own ``Changed`` message once its
        initial value settles — one per checkbox, not one for the whole
        rebuild — so without ``_suppress_events`` this would dispatch
        one redundant background render per channel on every run switch.
        The staleness guard in ``_apply_preview`` keeps any of those from
        landing *wrong*, but doing (and discarding) the work at all is still
        wasted worker threads and, worse, can flicker a stale frame onto the
        plot in between. Shares ``_suppress_events`` with ``refresh_runs()``'s own list-rebuild guard below, and the same idiom as ``ScopeScreen._suppress_dirty_events``.
        """
        grid = self.query_one("#data-channels", Grid)
        grid.remove_children()
        self._checkboxes = {}
        self._suppress_events = True
        try:
            for label in self._all_traces():
                checkbox = Checkbox(label, value=True, classes="data-chan-toggle")
                self._checkboxes[label] = checkbox
                grid.mount(checkbox)
        finally:
            self._suppress_events = False

    def _refresh_operand_choices(self) -> None:
        labels = sorted(self._all_traces())
        options = [(label, label) for label in labels]
        self.query_one("#data-chan-a", Select).set_options(options)
        self.query_one("#data-chan-b", Select).set_options(options)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not self._suppress_events and "data-chan-toggle" in event.checkbox.classes:
            self._redraw()

    # -- preview -----------------------------------------------------------------------------

    def _selected_traces(self) -> list[Trace]:
        all_traces = self._all_traces()
        return [
            all_traces[label]
            for label, checkbox in self._checkboxes.items()
            if checkbox.value and label in all_traces
        ]

    def _redraw(self) -> None:
        """Kick off a (possibly slow) preview render; never blocks the caller.

        Reading ``self._selected_traces()``/``self._path`` here, before
        handing off to the worker, is deliberate: both reflect live widget
        state that must be read on the main thread, and by the time a big
        render finishes several clicks could have changed either.
        """
        self._render_generation += 1
        generation = self._render_generation
        traces = self._selected_traces()
        title = self._path.name if self._path else None
        self._render_waveforms(generation, traces, title)

    @work(thread=True, exclusive=True, group="data-render", exit_on_error=False)
    def _render_waveforms(self, generation: int, traces: list[Trace], title: str | None) -> None:
        fig = build_waveform_figure(traces, title=title)
        try:
            lines, labels = figure_series(fig)
        finally:
            plt.close(fig)
        self._ui(self._apply_preview, generation, lines, labels, title)

    def _apply_preview(
        self,
        generation: int,
        lines: list[tuple[list[float], list[float], str | None]],
        labels: dict[str, str],
        title: str | None,
    ) -> None:
        if generation != self._render_generation:
            return  # superseded by a later _redraw() before this one finished
        plot = self.query_one("#data-plot", PlotextPlot)
        draw_series(
            plot,
            lines,
            title=title,
            xlabel=labels["xlabel"] or None,
            ylabel=labels["ylabel"] or None,
        )

    # -- math operations -----------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "data-apply-op":
            self._apply_operation()
        elif event.button.id == "data-clear-op":
            self._clear_derived()
        elif event.button.id == "data-export":
            self._export()

    def _operand(self, field_id: str, label: str) -> Trace:
        """The Trace named by a Channel/reference Select; a blank or unknown
        choice is a FieldError so the offending field gets marked."""
        name = self._selected(field_id, label)
        trace = self._all_traces().get(name)
        if trace is None:
            raise FieldError(field_id, f"{label}: {name!r} is not an available channel/trace")
        return trace

    def _apply_operation(self) -> None:
        log_widget = self.query_one("#data-log", RichLog)
        if not self._all_traces():
            self.notify("Select a scope acquisition with channel data first.", severity="error")
            return
        operation = self._selected("data-op", "Operation")
        try:
            if operation == "subtract":
                a = self._operand("data-chan-a", "Channel A")
                b = self._operand("data-chan-b", "Channel B")
                result = subtract_traces(a, b)
            elif operation == "background-region":
                a = self._operand("data-chan-a", "Channel A")
                lo = self._read("data-region-lo", _finite_float, "Region start")
                hi = self._read("data-region-hi", _finite_float, "Region end")
                result = subtract_background(a, region=(lo, hi))
            elif operation == "background-reference":
                a = self._operand("data-chan-a", "Channel A")
                reference = self._operand("data-chan-b", "Reference")
                result = subtract_background(a, reference=reference)
            else:
                a = self._operand("data-chan-a", "Channel A")
                x_scale = self._read("data-xscale", _finite_float, "X scale")
                x_offset = self._read("data-xoffset", _finite_float, "X offset")
                y_scale = self._read("data-yscale", _finite_float, "Y scale")
                y_offset = self._read("data-yoffset", _finite_float, "Y offset")
                result = scale_trace(
                    a, x_scale=x_scale, x_offset=x_offset, y_scale=y_scale, y_offset=y_offset
                )
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(str(exc), severity="error", markup=False)
            return
        except ValueError as exc:
            # A domain error (mismatched units, an empty background window, a
            # zero scale) rather than one field's fault — nothing to flag.
            self.notify(str(exc), severity="error", markup=False)
            return

        self._derived[result.label] = result
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw()
        log_widget.write(f"Added derived trace: {escape(result.label)}")

    def _clear_derived(self) -> None:
        self._derived = {}
        self._rebuild_channel_checkboxes()
        self._refresh_operand_choices()
        self._redraw()
        self.query_one("#data-log", RichLog).write("Cleared derived traces.")

    # -- export --------------------------------------------------------------------------------

    def _export(self) -> None:
        if self._path is None:
            self.notify("Select a scope acquisition first.", severity="error")
            return
        traces = self._selected_traces()
        if not traces:
            self.notify("Select at least one channel/trace to export.", severity="error")
            return
        out_path = self._path.with_name(f"{self._path.stem}-plot.png")
        title = self._path.name
        self.query_one("#data-export", Button).disabled = True
        self._save_export(traces, out_path, title)

    @work(thread=True, exclusive=True, group="data-export", exit_on_error=False)
    def _save_export(self, traces: list[Trace], out_path: Path, title: str) -> None:
        error: Exception | None = None
        try:
            save_waveform_figure(traces, out_path, title=title)
        except Exception as exc:  # noqa: BLE001 - reported, not raised, from a UI action
            log.exception("data: failed to export waveform figure")
            error = exc
        self._ui(self._finish_export, out_path, error)

    def _finish_export(self, out_path: Path, error: Exception | None) -> None:
        self.query_one("#data-export", Button).disabled = False
        log_widget = self.query_one("#data-log", RichLog)
        if error is not None:
            log_widget.write(f"[red]Export failed: {escape(str(error))}[/red]")
            self.notify(f"Export failed: {error}", severity="error", markup=False)
            return
        log_widget.write(f"Saved plot: {escape(str(out_path))}")
        self.notify(f"Saved {out_path.name}")
