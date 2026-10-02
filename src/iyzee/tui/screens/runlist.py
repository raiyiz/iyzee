"""Shared plumbing for the Results screen recording browser.

The list handling is subtle enough to keep in one place: see
RunListPage.refresh_runs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from textual.markup import escape
from textual.widgets import Label, ListItem, ListView, Static

from ...experiment.io import STEM_PATTERN
from .page import Page


def run_label(path: Path, mtime: float) -> str:
    """List label for one saved run: its name (if any) and save time."""
    when = datetime.fromtimestamp(mtime).astimezone()
    match = STEM_PATTERN.match(path.stem)
    name = match["name"] if match else None
    prefix = f"{escape(name)}  " if name else ""
    return f"{prefix}{when:%Y-%m-%d %H:%M:%S}"


class RunListPage(Page):
    """A page with a list of saved recordings beside a preview of the selected one.

    A subclass sets ``LIST_ID`` / ``HINT_ID`` (widget ids), implements
    ``_scan_runs() -> Iterable[Path]``, ``_hint_text() -> str``,
    ``_select(path)`` and ``_show_empty()`` (which also forgets the current
    selection), and calls ``_init_run_list()`` from ``on_mount`` before its
    first ``refresh_runs()``.
    """

    LIST_ID: str
    HINT_ID: str
    _scan_runs: Callable[[], Iterable[Path]]
    _hint_text: Callable[[], str]
    _select: Callable[[Path], None]
    _show_empty: Callable[[], None]

    def _init_run_list(self) -> None:
        self._paths: list[Path] = []
        self._path: Path | None = None
        self._suppress_events = False

    def on_show(self) -> None:
        self.refresh_runs()

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
        self._paths = sorted(self._scan_runs(), key=lambda p: p.stat().st_mtime, reverse=True)
        self.query_one(f"#{self.HINT_ID}", Static).update(self._hint_text())
        self._suppress_events = True
        try:
            list_view.clear()
            for path in self._paths:
                list_view.append(ListItem(Label(run_label(path, path.stat().st_mtime))))
        finally:
            self._suppress_events = False

        if self._paths:
            # ListItem mounting is deferred. Set the index only after the
            # refresh has completed, otherwise ListView may validate it
            # against an empty child list and leave the browser unhighlighted.
            self.call_after_refresh(self._finish_refresh, list_view, previous)
        else:
            self._show_empty()

    def _finish_refresh(self, list_view: ListView, previous: Path | None) -> None:
        """Select the preserved run once the rebuilt ListView is mounted."""
        if not self._paths:
            self._show_empty()
            return
        index = self._paths.index(previous) if previous in self._paths else 0
        self._suppress_events = True
        try:
            list_view.index = index
        finally:
            self._suppress_events = False
        self._select(self._paths[index])

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Follow Up/Down directly."""
        if self._suppress_events or event.list_view.id != self.LIST_ID:
            return
        index = event.list_view.index
        if index is not None and 0 <= index < len(self._paths):
            self._select(self._paths[index])
