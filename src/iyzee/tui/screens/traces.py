"""Traces screen: browse persisted Sweep and Scope recordings.

Reads the shared numeric ``.npz`` plus JSON-manifest storage produced by
``experiment.io``: sweep checkpoints keep their existing schema, while
Scope acquisitions use channel-specific time/value arrays and optional raw
waveform codes. There is one persistence format to browse, not a second
Traces-only representation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.markup import escape
from textual.widgets import Label, ListItem, ListView, Static
from textual_plotext import PlotextPlot

from ...experiment import difference_series_many
from ...experiment.io import DATA_ROOT, STEM_PATTERN, load_recording
from ..plotting import PlotSnapshot, draw_snapshot, make_plot_snapshot
from .page import Page

# The single source of truth for this path is experiment.io.DATA_ROOT — kept
# as a separate module-level name (rather than reading io.DATA_ROOT directly
# everywhere below) so it stays independently monkeypatchable in tests, same
# as before this file imported it instead of recomputing it. Not read via
# create_dirs() itself: that creates the directory (mkdir) as a side effect,
# which this screen — which only browses existing runs — must not trigger.
_DATA_ROOT = DATA_ROOT


def _run_label(path: Path, mtime: float) -> str:
    """List label for one saved run: its name (if any) and save time, e.g.
    ``bandwidth  2026-09-18 14:10:05``.

    The time comes from the file's own mtime (already read by
    ``refresh_runs`` to sort the list, and reused here rather than parsed
    back out of the filename — simpler, and still correct for a
    hand-placed or unusually-named file). Only the run's name, if any, is
    pulled from the filename (see ``io._new_stem``/``io.STEM_PATTERN``);
    a file whose stem doesn't have that shape is still listed, just
    without a name rather than being hidden or treated as an error.
    """
    when = datetime.fromtimestamp(mtime).astimezone()
    match = STEM_PATTERN.match(path.stem)
    name = match["name"] if match else None
    prefix = f"{escape(name)}  " if name else ""
    return f"{prefix}{when:%Y-%m-%d %H:%M:%S}"


class TracesScreen(Page):
    """List recorded runs on the left, preview the selected one on the right."""

    def compose(self) -> ComposeResult:
        yield Static("Traces", classes="panel-title")
        yield Static("", id="traces-hint", classes="hint")
        yield Horizontal(
            ListView(id="traces-list"),
            # Scrollable, not a plain Vertical: a run's metadata summary can
            # be arbitrarily long, and a Vertical would clip it.
            VerticalScroll(
                Static("Select a run to preview it.", id="traces-summary"),
                PlotextPlot(id="traces-plot"),
                id="traces-detail",
            ),
            id="traces-body",
        )

    def on_mount(self) -> None:
        self._paths: list[Path] = []
        self._render_generation = 0
        self.refresh_runs()

    def on_show(self) -> None:
        self.refresh_runs()

    def refresh_runs(self) -> None:
        """Re-scan the data directory and preserve the current selection."""
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
        list_view.clear()
        for path in self._paths:
            list_view.append(ListItem(Label(_run_label(path, path.stat().st_mtime))))
        if not self._paths:
            self._show_empty()
            return
        list_view.index = self._paths.index(previous) if previous in self._paths else 0

    def _show_empty(self) -> None:
        self._render_generation += 1
        self.query_one("#traces-summary", Static).update(
            "No recordings yet.\n\nSweep and Scope acquisitions are saved automatically "
            "and will appear here."
        )
        self.query_one("#traces-plot", PlotextPlot).plt.clear_data()
        self.query_one("#traces-plot", PlotextPlot).refresh()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "traces-list":
            return
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._paths):
            self._show(self._paths[index])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "traces-list":
            return
        if 0 <= event.index < len(self._paths):
            self._show(self._paths[event.index])

    def _show(self, path: Path) -> None:
        """Start preview preparation without doing file/numpy work on the UI thread."""
        self._render_generation += 1
        generation = self._render_generation
        self.query_one("#traces-summary", Static).update(
            f"[b]{escape(path.name)}[/b]\n\nLoading preview…"
        )
        self._render(path, generation)

    @work(thread=True, exclusive=True, group="traces-render", exit_on_error=False)
    def _render(self, path: Path, generation: int) -> None:
        try:
            recording = load_recording(path)

            if recording.metadata.get("kind") == "scope-acquisition":
                metadata = recording.metadata
                summary = self._scope_summary(path, metadata)
                first = next(
                    (w for w in metadata.get("waveforms", []) if isinstance(w, dict)), {}
                )
                series = []
                for waveform in metadata.get("waveforms", []):
                    if not isinstance(waveform, dict):
                        continue
                    channel = str(waveform.get("channel", "?"))
                    time_array = recording.arrays.get(f"time_{channel}")
                    value_array = recording.arrays.get(f"value_{channel}")
                    if time_array is None or value_array is None:
                        continue
                    series.append((time_array, value_array, channel))
                snapshot = make_plot_snapshot(
                    series,
                    title=path.name,
                    xlabel=f"Time ({first.get('time_unit', '')})",
                    ylabel=f"Signal ({first.get('value_unit', '')})",
                )
            else:
                arrays = recording.arrays
                x_values = arrays.get("x_values")
                if x_values is None:
                    raise ValueError("Missing x_values in recording.")
                traces = {
                    key[len("trace_") :]: value
                    for key, value in arrays.items()
                    if key.startswith("trace_")
                }
                points = recording.metadata.get("points", [])
                run_metadata = recording.metadata.get("run_metadata")
                lines = [f"[b]{escape(path.name)}[/b]", f"{len(x_values)} point(s)"]
                if isinstance(run_metadata, dict):
                    lines.append("")
                    lines.extend(
                        f"{escape(str(k))}: {escape(str(v))}" for k, v in run_metadata.items()
                    )
                summary = "\n".join(lines)

                labels = [
                    (points[index].get("label") or f"pt {index}")
                    if index < len(points) and isinstance(points[index], dict)
                    else f"pt {index}"
                    for index in range(len(x_values))
                ]
                series = difference_series_many(
                    traces.get("squeezing")
                    if traces.get("squeezing") is not None
                    else [None] * len(x_values),
                    traces.get("shot_noise")
                    if traces.get("shot_noise") is not None
                    else [None] * len(x_values),
                    labels,
                )
                snapshot = make_plot_snapshot(
                    series,
                    title=path.name,
                    xlabel="Trace point",
                    ylabel="Squeezing - shot noise",
                )
        except Exception as exc:  # noqa: BLE001 - preview errors are shown in the page
            self._ui(self._finish_render, path, generation, None, exc)
            return

        self._ui(self._finish_render, path, generation, snapshot, None, summary)

    def _scope_summary(self, path: Path, metadata: dict) -> str:
        lines = [f"[b]{escape(path.name)}[/b]", "Scope acquisition"]
        if metadata.get("measurement_id"):
            lines.append(f"Measurement: {escape(str(metadata['measurement_id']))}")
        if metadata.get("started_at_utc"):
            lines.append(f"Started: {escape(str(metadata['started_at_utc']))}")
        instrument = metadata.get("instrument")
        if isinstance(instrument, dict):
            lines.append(
                "Instrument: " + escape(str(instrument.get("address") or "address unknown"))
            )
        for waveform in metadata.get("waveforms", []):
            if not isinstance(waveform, dict):
                continue
            channel = str(waveform.get("channel", "?"))
            stats = waveform.get("stats")
            if isinstance(stats, dict):
                unit = str(waveform.get("value_unit", ""))
                lines.append(
                    f"{escape(channel)}: n={stats.get('sample_count', '?')}, "
                    f"min={stats.get('min', '?')} {escape(unit)}, "
                    f"max={stats.get('max', '?')} {escape(unit)}, "
                    f"p-p={stats.get('peak_to_peak', '?')} {escape(unit)}, "
                    f"rms={stats.get('rms', '?')} {escape(unit)}"
                )
        errors = metadata.get("errors")
        if errors:
            lines.append(f"Errors: {escape(str(errors))}")
        return "\n".join(lines)

    def _finish_render(
        self,
        path: Path,
        generation: int,
        snapshot: PlotSnapshot | None,
        error: Exception | None,
        summary: str | None = None,
    ) -> None:
        if generation != self._render_generation:
            return
        current_index = self.query_one("#traces-list", ListView).index
        current_path = (
            self._paths[current_index]
            if current_index is not None and 0 <= current_index < len(self._paths)
            else None
        )
        if current_path != path:
            return

        if error is not None:
            self.query_one("#traces-summary", Static).update(
                f"[b]{escape(path.name)}[/b]\n\n"
                f"[red]Could not read file: {escape(str(error))}[/red]"
            )
            plot = self.query_one("#traces-plot", PlotextPlot)
            plot.plt.clear_data()
            plot.refresh()
            return

        assert snapshot is not None
        self.query_one("#traces-summary", Static).update(summary or "")
        draw_snapshot(self.query_one("#traces-plot", PlotextPlot), snapshot)

