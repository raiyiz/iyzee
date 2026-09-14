"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import threading
from collections import defaultdict

from textual.app import App

from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen
from .instruments import InstrumentHandle
from .workers import LastRun


class IyzeeApp(App):
    """Connect to lab instruments, browse traces, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    # Keep navigation in Textual itself rather than interpreting keys in
    # ``on_key``. This means editable widgets retain their normal key
    # handling, while Footer and the command palette expose the same
    # navigation to mouse and keyboard users.
    BINDINGS = [
        ("c", "switch_mode('connect')", "Connect"),
        ("s", "switch_mode('sweep')", "Sweep"),
        ("t", "switch_mode('traces')", "Traces"),
        ("i", "switch_mode('console')", "Console"),
    ]
    MODES = {
        "connect": ConnectScreen,
        "sweep": SweepScreen,
        "traces": TracesScreen,
        "console": ConsoleScreen,
    }
    DEFAULT_MODE = "connect"

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

    def on_mount(self) -> None:
        self.switch_mode(self.DEFAULT_MODE)


def run() -> None:
    """Console-script entry point (``iyzee-tui``)."""
    IyzeeApp().run()


if __name__ == "__main__":
    run()
