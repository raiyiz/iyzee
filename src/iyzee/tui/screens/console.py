"""Full-screen embedded IPython console."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Static

from ..ipython import IyzeeIPython, namespace_from_handles
from ..widgets.console import IyzeeConsole


class ConsoleScreen(Screen):
    """A live Python/IPython console bound to the app's current instruments."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Embedded IPython • same process • same live device objects", classes="panel-title"
        )
        yield IyzeeConsole(IyzeeIPython())

    def on_mount(self) -> None:
        self.console = self.query_one(IyzeeConsole)
        self.console.refresh_namespace(namespace_from_handles(self.app.handles))

    def on_screen_resume(self) -> None:
        self.console.refresh_namespace(namespace_from_handles(self.app.handles))
