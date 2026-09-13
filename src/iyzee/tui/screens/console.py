"""Full-screen embedded IPython console."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Static

from ..ipython import IyzeeIPython
from ..widgets.console import IyzeeConsole


class ConsoleScreen(Screen):
    """A live Python/IPython console bound to the app's current instruments."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Embedded IPython • same process • lab.mx / lab.shutter / lab.scope",
            classes="panel-title",
        )
        # Constructed once — this only runs on the screen's first mount,
        # not on every switch_screen() back to it, so the shell (and its
        # variables, history, lab) genuinely persists for the app's
        # lifetime. lab itself needs no refreshing either way (see
        # LabProxy's docstring) — it reads app state live on every access.
        yield IyzeeConsole(IyzeeIPython(self.app))

    def on_mount(self) -> None:
        self.console = self.query_one(IyzeeConsole)

    def on_screen_resume(self) -> None:
        # Purely cosmetic: update the status line's "connected: ..." text.
        # Nothing about the shell itself needs refreshing.
        self.console.refresh_status()
