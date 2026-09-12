"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

from textual.app import App
from textual.events import Key
from textual.widget import Widget

from .instruments import InstrumentHandle
from .navigation.policy import NavigationPolicy
from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen


class IyzeeApp(App):
    """Connect to lab instruments, browse traces, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        ("c", "show_connect", "Connect"),
        ("s", "show_sweep", "Sweep"),
        ("t", "show_traces", "Traces"),
        ("i", "show_console", "IPython"),
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.handles: dict[str, InstrumentHandle] = {}
        self._navigation_policy = NavigationPolicy()
        self._connect_screen = ConnectScreen()
        self._sweep_screen = SweepScreen()
        self._traces_screen = TracesScreen()
        self._console_screen = ConsoleScreen()

    def on_mount(self) -> None:
        self.push_screen(self._connect_screen)

    def on_key(self, event: Key) -> None:
        """Handle navigation keys only when the focused widget is not editing."""
        focused: Widget | None = self.screen.focused if self.screen else None
        if self._navigation_policy.is_insert(focused):
            return

        if event.key == "j":
            self.screen.focus_next()
            event.stop()
        elif event.key == "k":
            self.screen.focus_previous()
            event.stop()
        elif event.key == ":":
            self.action_command_palette()
            event.stop()

    def action_show_connect(self) -> None:
        self.switch_screen(self._connect_screen)

    def action_show_sweep(self) -> None:
        self.switch_screen(self._sweep_screen)

    def action_show_traces(self) -> None:
        self.switch_screen(self._traces_screen)

    def action_show_console(self) -> None:
        self.switch_screen(self._console_screen)


def run() -> None:
    """Console-script entry point (``iyzee-tui``)."""
    IyzeeApp().run()


if __name__ == "__main__":
    run()
