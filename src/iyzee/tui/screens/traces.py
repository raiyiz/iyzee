"""Traces screen: browse previously recorded sweep runs.

Reads exactly what ``experiment.io.save_step_results`` already
writes — the compressed ``.npz`` archives with per-point metadata — so
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
from ...experiment.io import DATA_ROOT
from ..plotting import draw_series
from .page import Page

# The single source of truth for this path is experiment.io.DATA_ROOT — kept
# as a separate module-level name (rather than reading io.DATA_ROOT directly
# everywhere below) so it stays independently monkeypatchable in tests, same
# as before this file imported it instead of recomputing it. Not read via
# create_dirs() itself, which always creates a fresh dated directory — a side
# effect this screen (which only browses existing runs) must not trigger.
_DATA_ROOT = DATA_ROOT


def _run_label(path: Path) -> str:
    """List label for one saved run: ``<run folder>  HH:MM:SS``.

    Files are named by their save timestamp (``20260918T141005.npz``),
    which is unreadable at a glance; the time is what tells two runs in the
    same folder apart. Anything that doesn't parse falls back to the name.
    """
    folder = escape(path.parent.name)
    try:
        when = datetime.strptime(path.stem, "%Y%m%dT%H%M%S")
    except ValueError:
        return f"{folder}/{escape(path.name)}"
    return f"{folder}  {when:%H:%M:%S}"


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
            list_view.append(ListItem(Label(_run_label(path))))
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
            with np.load(path, allow_pickle=True) as archive:
                data = archive["data"]
                metadata = archive["metadata"] if "metadata" in archive else None
                run_metadata = archive["run_metadata"] if "run_metadata" in archive else None
        except Exception as exc:  # noqa: BLE001
            summary.update(
                f"[b]{escape(path.name)}[/b]\n\n[red]Could not read file: {escape(str(exc))}[/red]"
            )
            plot.plt.clear_data()
            plot.refresh()
            return

        lines = [f"[b]{escape(path.name)}[/b]", f"{len(data)} point(s)"]
        if run_metadata is not None:
            try:
                meta = json.loads(str(run_metadata))
                lines.append("")
                lines.extend(f"{escape(str(k))}: {escape(str(v))}" for k, v in meta.items())
            except ValueError, TypeError:
                pass
        summary.update("\n".join(lines))

        series = []
        for index, point in enumerate(data):
            _x_value, squeezing, shot_noise = point
            label = None
            if metadata is not None and index < len(metadata):
                label = metadata[index].get("label")
            result = difference_series(squeezing, shot_noise, label or f"pt {index}")
            if result is not None:
                series.append(result)
        draw_series(
            plot,
            series,
            title=path.name,
            xlabel="Trace point",
            ylabel="Squeezing - shot noise",
        )
