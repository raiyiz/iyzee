"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Header, Static

from .instruments import INSTRUMENTS, InstrumentHandle
from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen
from .workers import LastRun


class NavRail(Static):
    """Persistent left-hand page list + a per-instrument connection dot.

    This is a visual complement to the existing c/s/t/i (or F1-F4)
    shortcuts, not a replacement for them — clicking a row calls the same
    ``IyzeeApp.action_show_page`` those bindings do, but the rail itself
    isn't in the tab/focus chain, keeping the escape/j/k navigation added
    earlier untouched. Instrument dots are refreshed opportunistically
    whenever a page is switched (see ``action_show_page``), not on every
    connect/disconnect as it happens elsewhere in the app — a deliberate
    simplification: fully live dots would need a callback wired from
    ConnectScreen into this widget for one cosmetic detail.
    """

    PAGES = (
        ("connect", "Connect"),
        ("sweep", "Sweep"),
        ("traces", "Traces"),
        ("console", "Console"),
    )

    def compose(self) -> ComposeResult:
        yield Static("iyzee", id="nav-title")
        for page_id, label in self.PAGES:
            yield Static(label, id=f"nav-{page_id}", classes="nav-item")
        yield Static("", id="nav-instruments")

    def on_mount(self) -> None:
        self.set_active("connect")
        self.refresh_instruments()

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None or not widget.has_class("nav-item") or widget.id is None:
            return
        page_id = widget.id.removeprefix("nav-")
        cast("IyzeeApp", self.app).action_show_page(page_id)

    def set_active(self, page_id: str) -> None:
        for pid, _ in self.PAGES:
            self.query_one(f"#nav-{pid}", Static).set_class(pid == page_id, "-active")

    def refresh_instruments(self) -> None:
        app = cast("IyzeeApp", self.app)
        lines = [
            f"{spec.label} {'●' if spec.key in app.handles else '○'}" for spec in INSTRUMENTS
        ]
        self.query_one("#nav-instruments", Static).update("\n".join(lines))


class IyzeeApp(App):
    """Connect to lab instruments, browse traces, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    # Textual's command palette defaults to Ctrl+P with a *priority*
    # binding — priority bindings are checked before a focused widget ever
    # gets a look at the key, regardless of the DOM/focus chain — which
    # silently ate every Ctrl+P the console's own "previous history entry"
    # binding was supposed to get (Ctrl+N happens not to collide with
    # anything else Textual claims by default, so only this half of the
    # history pair needed moving). The console's binding is the one
    # documented and depended on elsewhere in this app, so the palette
    # moves instead of it.
    COMMAND_PALETTE_BINDING = "ctrl+backslash"

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
        Binding("c", "show_page('connect')", "Connect"),
        Binding("s", "show_page('sweep')", "Sweep"),
        Binding("t", "show_page('traces')", "Traces"),
        Binding("i", "show_page('console')", "Console"),
        Binding("f1", "show_page('connect')", "Connect", priority=True),
        Binding("f2", "show_page('sweep')", "Sweep", priority=True),
        Binding("f3", "show_page('traces')", "Traces", priority=True),
        Binding("f4", "show_page('console')", "Console", priority=True),
        Binding("escape", "blur_focused", "Leave field", show=False),
        Binding("j", "focus_next", "Focus next", show=False),
        Binding("k", "focus_previous", "Focus previous", show=False),
    ]

    # One page widget per entry, held all at once inside a ContentSwitcher
    # instead of the old App.MODES (a separate, independently-mounted
    # Screen per page). This is what makes a persistent NavRail possible —
    # MODES/switch_mode() tears down and rebuilds the whole screen on every
    # switch, so nothing outside the switched screen could ever persist.
    PAGES = {
        "connect": ConnectScreen,
        "sweep": SweepScreen,
        "traces": TracesScreen,
        "console": ConsoleScreen,
    }
    DEFAULT_PAGE = "connect"

    def __init__(self) -> None:
        super().__init__()
        self.handles: dict[str, InstrumentHandle] = {}
        # One lock per instrument key, shared by every caller that talks to
        # that instrument's hardware: a page's background worker (Connect,
        # Sweep) and the IPython console via LockedProxy (see
        # instruments.LockedProxy and ipython.namespace_from_handles). This
        # is what stops the console and a page from issuing overlapping
        # commands to the same physical instrument from two threads at once.
        # defaultdict so any caller can address a key before that
        # instrument has ever been connected, without pre-populating one
        # lock per entry in instruments.INSTRUMENTS here.
        self.instrument_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        # The most recently completed sweep, if any — set by
        # SweepScreen._finish, read by ConsoleScreen to expose `results` in
        # the console namespace. See workers.LastRun.
        self.last_run: LastRun | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app-body"):
            yield NavRail(id="nav-rail")
            with ContentSwitcher(initial=self.DEFAULT_PAGE, id="page-switcher"):
                for page_id, page_cls in self.PAGES.items():
                    yield page_cls(id=page_id)
        yield Footer()

    def action_show_page(self, page_id: str) -> None:
        self.query_one(ContentSwitcher).current = page_id
        nav = self.query_one(NavRail)
        nav.set_active(page_id)
        nav.refresh_instruments()

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
