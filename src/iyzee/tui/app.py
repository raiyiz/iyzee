"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import threading
from collections import defaultdict

from textual.app import App
from textual.binding import Binding

from .instruments import InstrumentHandle
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

    # Keep navigation in Textual itself rather than interpreting keys in
    # ``on_key``. This means editable widgets retain their normal key
    # handling, while Footer and the command palette expose the same
    # navigation to mouse and keyboard users.
    #
    # "escape" and "j"/"k" specifically: Textual's own Input/TextArea
    # widgets don't bind Escape to anything, so it already bubbles here
    # untouched — this doesn't intercept a key those widgets wanted.
    # "j"/"k" bind to the same focus_next/focus_previous actions Tab/
    # Shift+Tab already use; an editable widget consumes plain "j"/"k"
    # keystrokes itself (typing the character) before they ever reach
    # this binding, so it only fires when nothing is claiming them —
    # i.e. exactly the "outside editable widgets" case.
    BINDINGS = [
        Binding("c", "switch_mode('connect')", "Connect"),
        Binding("s", "switch_mode('sweep')", "Sweep"),
        Binding("t", "switch_mode('traces')", "Traces"),
        Binding("i", "switch_mode('console')", "Console"),
        Binding("f1", "switch_mode('connect')", "Connect", priority=True),
        Binding("f2", "switch_mode('sweep')", "Sweep", priority=True),
        Binding("f3", "switch_mode('traces')", "Traces", priority=True),
        Binding("f4", "switch_mode('console')", "Console", priority=True),
        Binding("escape", "blur_focused", "Leave field", show=False),
        Binding("j", "focus_next", "Focus next", show=False),
        Binding("k", "focus_previous", "Focus previous", show=False),
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

    def action_blur_focused(self) -> None:
        """Leave the currently focused field, if any.

        This is what makes "escape" a real "back to navigation" motion for
        plain Input fields (RBW, IP addresses, ...), matching the escape
        the console's vim mode already has — those widgets have no notion
        of their own to leave, so without this, focusing one meant "j"/"k"
        typed into it forever instead of moving focus.
        """
        if self.focused is not None:
            self.focused.blur()


def run() -> None:
    """Console-script entry point (``iyzee-tui``)."""
    IyzeeApp().run()


if __name__ == "__main__":
    run()
