"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import threading
from collections import defaultdict

from textual.app import App
from textual.events import Key
from textual.widget import Widget

from .instruments import InstrumentHandle
from .navigation.policy import NavigationPolicy
from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen
from .workers import LastRun


class IyzeeApp(App):
    """Connect to lab instruments, browse traces, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self.handles: dict[str, InstrumentHandle] = {}
        # One lock per instrument key, shared by every caller that talks to
        # that instrument's hardware: a screen's background worker (Connect,
        # Sweep) and the IPython console via LockedProxy (see
        # instruments.LockedProxy and ipython.namespace_from_handles). This
        # is what stops the console and a screen from issuing overlapping
        # commands to the same physical instrument from two threads at once.
        # defaultdict so any caller can address a key before that
        # instrument has ever been connected, without pre-populating one
        # lock per entry in instruments.INSTRUMENTS here.
        self.instrument_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        # The most recently completed sweep, if any — set by
        # SweepScreen._finish, read by ConsoleScreen to expose `results` in
        # the console namespace. See workers.LastRun.
        self.last_run: LastRun | None = None
        self._navigation_policy = NavigationPolicy()
        self._connect_screen = ConnectScreen()
        self._sweep_screen = SweepScreen()
        self._traces_screen = TracesScreen()
        self._console_screen = ConsoleScreen()

    def on_mount(self) -> None:
        self.push_screen(self._connect_screen)

    def on_key(self, event: Key) -> None:
        """Handle global keys only when the focused widget is not editing."""
        focused: Widget | None = self.screen.focused if self.screen else None
        if focused is not None and self._navigation_policy.is_insert(focused):
            if event.key == "escape" and focused.id != "console-input":
                focused.blur()
                event.stop()
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
        elif event.key == "q":
            self.exit()
            event.stop()
        elif event.key == "c":
            self.action_show_connect()
            event.stop()
        elif event.key == "s":
            self.action_show_sweep()
            event.stop()
        elif event.key == "t":
            self.action_show_traces()
            event.stop()
        elif event.key == "i":
            self.action_show_console()
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
