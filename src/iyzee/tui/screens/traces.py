"""Traces screen: browse previously recorded sweep runs.

Reads exactly what ``experiment.io.save_step_results`` already writes —
each run's numeric ``.npz`` plus its ``.json`` metadata sidecar — so
there's no new persistence format to maintain just for browsing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.markup import escape
from textual.widgets import Label, ListItem, ListView, Static
from textual_plotext import PlotextPlot

from ...experiment import difference_series
from ...experiment.io import DATA_ROOT, STEM_PATTERN
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
        self.refresh_runs()

    def on_show(self) -> None:
        self.refresh_runs()

    def refresh_runs(self) -> None:
        """Re-scan the data directory. Cheap enough to call on every visit.

        Keeps the highlighted run highlighted across the rescan (falling
        back to the newest), so coming back to this page doesn't silently
        move the highlight away from the run the preview is showing.
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
        list_view.clear()
        for path in self._paths:
            list_view.append(ListItem(Label(_run_label(path, path.stat().st_mtime))))
        if not self._paths:
            self._show_empty()
            return
        # append() does not set a highlighted index the way passing
        # children to ListView's constructor does, so without this nothing
        # is "current". Setting it also fires Highlighted, which is what
        # draws the preview (see on_list_view_highlighted).
        list_view.index = self._paths.index(previous) if previous in self._paths else 0

    def _show_empty(self) -> None:
        self.query_one("#traces-summary", Static).update(
            "No runs recorded yet.\n\nRun a sweep on the Sweep page (F2); "
            "it is saved as it goes and will appear here."
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
        summary = self.query_one("#traces-summary", Static)
        plot = self.query_one("#traces-plot", PlotextPlot)

        try:
            with np.load(path, allow_pickle=False) as archive:
                x_values = archive["x_values"]
                traces = {
                    key[len("trace_") :]: archive[key]
                    for key in archive.files
                    if key.startswith("trace_")
                }
        except Exception as exc:  # noqa: BLE001
            summary.update(
                f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]"
            )
            plot.plt.clear_data()
            plot.refresh()
            return

        # A missing or corrupt sidecar (partial write, hand edit) only costs
        # the per-point labels and the run-metadata summary lines, not the
        # numeric data above — that's already loaded and shown regardless.
        points: list[dict] = []
        run_metadata = None
        try:
            sidecar = json.loads(path.with_suffix(".json").read_text())
            points = sidecar.get("points", [])
            run_metadata = sidecar.get("run_metadata")
        except OSError, ValueError:
            pass

        lines = [f"[b]{escape(path.name)}[/b]", f"{len(x_values)} point(s)"]
        if isinstance(run_metadata, dict):
            lines.append("")
            lines.extend(f"{escape(str(k))}: {escape(str(v))}" for k, v in run_metadata.items())
        summary.update("\n".join(lines))

        squeezing = traces.get("squeezing")
        shot_noise = traces.get("shot_noise")
        series = []
        for index in range(len(x_values)):
            label = None
            if index < len(points):
                label = points[index].get("label")
            sq = squeezing[index] if squeezing is not None else None
            sn = shot_noise[index] if shot_noise is not None else None
            result = difference_series(sq, sn, label or f"pt {index}")
            if result is not None:
                series.append(result)
        draw_series(
            plot,
            series,
            title=path.name,
            xlabel="Trace point",
            ylabel="Squeezing - shot noise",
        )
