"""Traces screen: browse persisted Sweep and Scope recordings.

Reads the shared numeric ``.npz`` plus JSON-manifest storage produced by
``experiment.io``: sweep checkpoints keep their existing schema, while
Scope acquisitions use channel-specific time/value arrays and optional raw
waveform codes. There is one persistence format to browse, not a second
Traces-only representation.

Loading a run and turning it into something drawable is pure computation
(``_prepare_view``/``_prepare_scope_view`` below: no Textual import, no
widget touched) split out specifically so it can run in a
``@work(thread=True)`` worker rather than in the UI event handler that
requests it. That split matters here because ``ListView`` posts its own
``Highlighted`` message on every change to ``.index`` — including changes
it makes to itself while a freshly (re)built list is being populated, not
just deliberate navigation — so without ``_select``'s idempotency check
(same path already showing -> skip) and the worker's own generation guard
(see ``_apply_view``), one visit to this page could dispatch several
redundant reloads of a potentially large file, and, worse, let a slow one
finish and overwrite a faster, more recent one's result. Same reasoning,
and the same two guards, as ``tui.screens.data.DataScreen``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.markup import escape
from textual.widgets import Label, ListItem, ListView, Static
from textual_plotext import PlotextPlot

from ...experiment import difference_series_many
from ...experiment.io import DATA_ROOT, STEM_PATTERN, load_recording
from ..plotting import draw_series
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


@dataclass(frozen=True)
class _PreparedView:
    """Everything :meth:`TracesScreen._apply_view` needs to update the
    widgets for one run — built in a worker thread, applied on the main one.
    ``series is None`` means "clear the plot" (nothing to draw, or the run
    couldn't be read at all); rich-markup formatting is already baked into
    ``summary`` since that's cheap and keeps ``_apply_view`` from needing to
    know anything about *why* it's showing what it's showing.
    """

    summary: str
    series: Sequence[tuple[Sequence[float], Sequence[float], str | None]] | None
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""


def _prepare_scope_view(path: Path, arrays: dict[str, np.ndarray], metadata: dict) -> _PreparedView:
    """Pure counterpart of the old ``_show_scope_recording``: build a
    :class:`_PreparedView` for a durable scope acquisition, touching no
    widget."""
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
        series.append((time_array.tolist(), value_array.tolist(), channel))
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
    """Pure counterpart of the old ``_show``: load ``path`` and build a
    :class:`_PreparedView`, touching no widget. Runs in a worker thread —
    see the module docstring for why.
    """
    try:
        recording = load_recording(path)
    except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]",
            series=None,
        )

    arrays = recording.arrays
    x_values = arrays.get("x_values")
    traces = {
        key[len("trace_") :]: value for key, value in arrays.items() if key.startswith("trace_")
    }
    # A missing or corrupt sidecar (partial write, hand edit) — already
    # tolerated by load_recording() — only costs the per-point labels and
    # the run-metadata summary lines below, not the numeric data above.
    sidecar = recording.metadata
    points: list[dict] = sidecar.get("points", [])
    run_metadata = sidecar.get("run_metadata")

    if sidecar.get("kind") == "scope-acquisition":
        return _prepare_scope_view(path, arrays, sidecar)
    if x_values is None:
        return _PreparedView(
            summary=f"[b]{escape(path.name)}[/b]\n\n[red]Missing x_values in recording.[/red]",
            series=None,
        )
    lines = [f"[b]{escape(path.name)}[/b]", f"{len(x_values)} point(s)"]
    if isinstance(run_metadata, dict):
        lines.append("")
        lines.extend(f"{escape(str(k))}: {escape(str(v))}" for k, v in run_metadata.items())

    squeezing = traces.get("squeezing")
    shot_noise = traces.get("shot_noise")
    labels = [
        (points[index].get("label") or f"pt {index}") if index < len(points) else f"pt {index}"
        for index in range(len(x_values))
    ]
    rows = len(x_values)
    series = difference_series_many(
        cast(Any, squeezing) if squeezing is not None else [None] * rows,
        cast(Any, shot_noise) if shot_noise is not None else [None] * rows,
        labels,
    )
    return _PreparedView(
        summary="\n".join(lines),
        series=series,
        title=path.name,
        xlabel="Trace point",
        ylabel="Squeezing - shot noise",
    )


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
        self._path: Path | None = None
        self._render_generation = 0
        self._suppress_events = False
        self.refresh_runs()

    def on_show(self) -> None:
        self.refresh_runs()

    def refresh_runs(self) -> None:
        """Re-scan the data directory. Cheap enough to call on every visit.

        Keeps the highlighted run highlighted across the rescan (falling
        back to the newest), so coming back to this page doesn't silently
        move the highlight away from the run the preview is showing.

        Rebuilds the list with ``_suppress_events`` set, then makes the one
        call that actually matters — to ``_select``/``_show_empty`` — itself,
        once, explicitly; see the module docstring for why ``ListView``'s
        own ``Highlighted`` message isn't trusted to do that reliably by
        itself.
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
                # append() does not set a highlighted index the way passing
                # children to ListView's constructor does, so without this
                # nothing is "current".
                list_view.index = self._paths.index(previous) if previous in self._paths else 0
        finally:
            self._suppress_events = False

        if self._paths:
            self._select(self._paths[list_view.index or 0])
        else:
            self._path = None
            self._show_empty()

    def _show_empty(self) -> None:
        self.query_one("#traces-summary", Static).update(
            "No recordings yet.\n\nSweep and Scope acquisitions are saved automatically "
            "and will appear here."
        )
        plot = self.query_one("#traces-plot", PlotextPlot)
        plot.plt.clear_data()
        plot.refresh()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Preview follows the highlight, so Up/Down browses runs directly.

        Previously only Enter/click selected, so the preview and the
        highlighted row could disagree — and on arrival the newest run was
        highlighted while the preview still said "Select a run".
        """
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
            # Reached again for the run already showing — most often ListView
            # re-posting Highlighted for an index that didn't actually change,
            # or on_mount and on_show both scanning at once (see the module
            # docstring). Filenames are timestamped and never rewritten under
            # the same name, so "same path" reliably means "nothing to redo".
            return
        self._path = path
        self._render_generation += 1
        generation = self._render_generation
        self._load_and_prepare(generation, path)

    @work(thread=True, exclusive=True, group="traces-load", exit_on_error=False)
    def _load_and_prepare(self, generation: int, path: Path) -> None:
        prepared = _prepare_view(path)
        self._ui(self._apply_view, generation, path, prepared)

    def _apply_view(self, generation: int, path: Path, prepared: _PreparedView) -> None:
        if generation != self._render_generation:
            return  # superseded by a later _select() before this one finished loading
        self.query_one("#traces-summary", Static).update(prepared.summary)
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
