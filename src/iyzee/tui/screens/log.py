"""Log screen: the app's own logging, made visible.

Nothing here logs anything itself — it displays whatever ``TuiLogHandler``
(installed once, at app startup — see ``logging_support.install``) has
already captured: the session's in-memory buffer by default, or an older
rotated file from ``logging_support.LOG_ROOT`` for history.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Button, RichLog, Select, Static

from .. import logging_support
from ..logging_support import LogEntry
from .page import Page

if TYPE_CHECKING:
    from ..app import IyzeeApp

_LIVE = "__live__"

_LEVEL_CHOICES = [
    ("All", "0"),
    ("Info", str(logging.INFO)),
    ("Warning", str(logging.WARNING)),
    ("Error", str(logging.ERROR)),
]


class LogScreen(Page):
    """The session's captured log, live, plus older rotated files to browse.

    "Live session" (the default, and what reopening this page always goes
    back to) tails ``TuiLogHandler``'s buffer and keeps growing as new
    records arrive, exactly like the per-screen ``#sweep-log``/
    ``#scope-log`` widgets already do for their own narrower, action-scoped
    feedback — this is the same idea, widened to everything the whole app
    logs. Picking an older file instead shows a fixed snapshot: it won't
    grow, and switching back to "Live session" is what to do next rather
    than waiting on it.
    """

    @property
    def iyzee_app(self) -> IyzeeApp:
        return cast("IyzeeApp", self.app)

    def compose(self) -> ComposeResult:
        yield Static("Log", classes="panel-title")
        with Horizontal(id="log-controls"):
            yield Select([("Live session", _LIVE)], value=_LIVE, allow_blank=False, id="log-source")
            yield Select(_LEVEL_CHOICES, value=str(logging.INFO), allow_blank=False, id="log-level")
            yield Button("Refresh", id="log-refresh")
        yield RichLog(id="log-view", highlight=False, markup=True, wrap=True)

    def on_mount(self) -> None:
        # _refresh_sources() mutates the "log-source" Select's options and
        # value, and Select posts Changed synchronously the moment its
        # value actually changes — including this first, programmatic
        # change from its compose()-time default. Left unguarded, that
        # reenters on_select_changed -> _repaint() *during* on_mount,
        # before the rest of this widget (and, in the running app,
        # `self.app`'s own setup) is necessarily finished — which is
        # exactly what broke this screen the first time around. _ready
        # gates that: nothing repaints until on_mount deliberately does so
        # itself, once, last.
        self._ready = False
        self._refresh_sources()
        self._ready = True
        self._repaint()

    def on_show(self) -> None:
        self._refresh_sources()
        self._repaint()

    def append_entry(self, entry: LogEntry) -> None:
        """Called by ``IyzeeApp`` for every new record, live, from
        ``TuiLogHandler``. A no-op unless "Live session" is the current
        source and the entry clears the level filter — the record is
        already in the shared buffer regardless (that's ``TuiLogHandler``'s
        job, not this screen's), so switching back to "Live session" later
        still shows it; this only governs what appears immediately.

        ``IyzeeApp`` finds this screen with ``query()``, not ``query_one``,
        so it's already a no-op if ``LogScreen`` itself isn't mounted at
        all yet (an early-startup log call, before ``ContentSwitcher`` has
        gotten to this page — it mounts last, see ``IyzeeApp.PAGES``). The
        narrower case this guards is this widget existing but its own
        ``compose()``-yielded children not having finished mounting yet —
        the same gap ``console.py``'s ``_render_status`` hit for real; see
        its comment for why "attached" and "children exist" aren't the
        same moment.
        """
        try:
            source = self.query_one("#log-source", Select)
        except NoMatches:
            return
        if source.value != _LIVE:
            return
        if entry.level < self._min_level():
            return
        self.query_one("#log-view", RichLog).write(entry.rich_markup())

    def _min_level(self) -> int:
        return int(self.query_one("#log-level", Select).value)

    def _refresh_sources(self) -> None:
        """Re-scan LOG_ROOT for rotated files, keeping the current
        selection if it still exists. Cheap enough to call on every visit
        rather than needing its own button, though "Refresh" also triggers
        it explicitly for someone who leaves this page open.

        Reads ``logging_support.LOG_ROOT`` through the module, not a bare
        imported name — a bare ``from ..logging_support import LOG_ROOT``
        would bind this module's own copy of the value once, at import
        time, and never see it change again, which is exactly what
        ``monkeypatch.setattr(logging_support, "LOG_ROOT", ...)`` (every
        test's isolation fixture — see ``conftest.py``) depends on being
        able to do.
        """
        select = self.query_one("#log-source", Select)
        current = select.value
        log_root = logging_support.LOG_ROOT
        files = sorted(log_root.glob("iyzee.log*"), reverse=True) if log_root.is_dir() else []
        options = [("Live session", _LIVE)] + [(f.name, str(f)) for f in files]
        select.set_options(options)
        select.value = current if current in {value for _label, value in options} else _LIVE

    def _repaint(self) -> None:
        """Clear and redraw #log-view from the current source + level filter.

        Named ``_repaint``, not ``_render`` — ``Widget`` already has its
        own private ``_render()`` (part of Textual's own rendering
        pipeline: ``_render_content()`` calls it to get the "visual" to
        draw). A same-named method here would silently override that
        instead of adding a new one, so Textual's compositor would end up
        calling *this* method to ask what to draw, get back this method's
        `None` return value, and crash trying to render `None`. It did,
        the first time this screen existed.
        """
        view = self.query_one("#log-view", RichLog)
        view.clear()
        source = self.query_one("#log-source", Select).value
        if source == _LIVE:
            min_level = self._min_level()
            for entry in self.iyzee_app.log_handler.records:
                if entry.level >= min_level:
                    view.write(entry.rich_markup())
            return
        try:
            text = Path(source).read_text(errors="replace")
        except OSError as exc:
            view.write(f"[red]Could not read {escape(source)}: {escape(str(exc))}[/red]")
            return
        view.write(escape(text) or "[dim](empty file)[/dim]")

    def on_select_changed(self, event: Select.Changed) -> None:
        if getattr(self, "_ready", False) and event.select.id in ("log-source", "log-level"):
            self._repaint()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "log-refresh":
            self._refresh_sources()
            self._repaint()
