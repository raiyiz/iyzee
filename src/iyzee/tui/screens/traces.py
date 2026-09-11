"""Traces screen: browse previously recorded sweep runs.

Reads exactly what ``experiment.persistence.save_step_results`` already
writes — the compressed ``.npz`` archives with per-point metadata — so
there's no new persistence format to maintain just for browsing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static
from textual_plotext import PlotextPlot

# <package_root>/data — matches iyzee.experiment.persistence.create_dirs(),
# without calling it (create_dirs() always creates a fresh dated directory,
# which we don't want as a side effect of just opening this screen).
_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


class TracesScreen(Screen):
    """List recorded runs on the left, preview the selected one on the right."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Traces", classes="panel-title")
        yield Horizontal(
            ListView(id="traces-list"),
            Vertical(
                Static("Select a run to preview it.", id="traces-summary"),
                PlotextPlot(id="traces-plot"),
                id="traces-detail",
            ),
            id="traces-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._paths: list[Path] = []
        self.refresh_runs()

    def on_screen_resume(self) -> None:
        self.refresh_runs()

    def refresh_runs(self) -> None:
        """Re-scan the data directory. Cheap enough to call on every visit."""
        self._paths = sorted(
            _DATA_ROOT.glob("**/*.npz"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        list_view = self.query_one("#traces-list", ListView)
        list_view.clear()
        for path in self._paths:
            run_dir = path.parent.name
            list_view.append(ListItem(Label(f"{run_dir}/{path.name}")))
        if self._paths:
            # append() does not set a highlighted index the way passing
            # children to ListView's constructor does, so without this
            # nothing is "current" and pressing Enter has no row to select.
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "traces-list":
            return
        if 0 <= event.index < len(self._paths):
            self._show(self._paths[event.index])

    def _show(self, path: Path) -> None:
        summary = self.query_one("#traces-summary", Static)
        plot = self.query_one("#traces-plot", PlotextPlot)
        plot.plt.clear_data()

        try:
            with np.load(path, allow_pickle=True) as archive:
                data = archive["data"]
                metadata = archive["metadata"] if "metadata" in archive else None
                run_metadata = archive["run_metadata"] if "run_metadata" in archive else None
        except Exception as exc:  # noqa: BLE001
            summary.update(f"[b]{path.name}[/b]\n\n[red]Could not read file: {exc}[/red]")
            plot.refresh()
            return

        lines = [f"[b]{path.name}[/b]", f"{len(data)} point(s)"]
        if run_metadata is not None:
            try:
                meta = json.loads(str(run_metadata))
                lines.append("")
                lines.extend(f"{k}: {v}" for k, v in meta.items())
            except ValueError, TypeError:
                pass
        summary.update("\n".join(lines))

        for index, point in enumerate(data):
            x_value, squeezing, shot_noise = point
            if squeezing is None or shot_noise is None:
                continue
            difference = np.asarray(squeezing) - np.asarray(shot_noise)
            label = None
            if metadata is not None and index < len(metadata):
                label = metadata[index].get("label")
            plot.plt.plot(
                list(range(len(difference))), list(difference), label=label or f"pt {index}"
            )
        plot.plt.title(path.name)
        plot.plt.xlabel("Trace point")
        plot.refresh()
