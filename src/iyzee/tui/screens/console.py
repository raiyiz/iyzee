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
        self.console.refresh_namespace(self._namespace())

    def on_screen_resume(self) -> None:
        self.console.refresh_namespace(self._namespace())

    def _namespace(self) -> dict[str, object]:
        namespace = namespace_from_handles(self.app.handles, self.app.instrument_locks)
        # The most recently completed sweep, if any — so e.g.
        # np.mean(results[-1].traces["squeezing"]) works right after a
        # sweep without leaving the console. See workers.LastRun and
        # IyzeeIPython.LIVE_NAMES.
        last_run = self.app.last_run
        namespace["results"] = last_run.results if last_run is not None else []
        namespace["last_run"] = last_run
        return namespace
