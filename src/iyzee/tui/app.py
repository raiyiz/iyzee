"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.markup import escape
from textual.widgets import ContentSwitcher, Footer, Header, Static

from .instruments import INSTRUMENTS, InstrumentHandle
from .ipython import default_history_file
from .ipython_session import IPythonSession
from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.sweep import SweepScreen
from .screens.traces import TracesScreen
from .workers import LastRun

log = logging.getLogger("iyzee.tui")


class NavRail(Static):
    """Persistent left-hand page list + a per-instrument connection dot.

    This is a visual complement to the existing c/s/t/i (or F1-F4)
    shortcuts, not a replacement for them — clicking a row calls the same
    ``IyzeeApp.action_show_page`` those bindings do, but the rail itself
    isn't in the tab/focus chain, keeping the escape/j/k navigation added
    earlier untouched. The instrument dots are live: ConnectScreen calls
    ``IyzeeApp.instruments_changed`` whenever a connection is made or
    dropped, and they are also refreshed on every page switch.
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
        """Redraw the "Instruments" block: a status dot *before* a short name.

        The dot leads the line so it can never be stranded on a wrapped
        second line away from the name it describes; short names keep each
        entry to a single line in the 18-column rail. The dot differs in
        shape (filled/hollow) as well as colour, so it doesn't rely on
        colour vision.
        """
        app = cast("IyzeeApp", self.app)
        lines = ["[b]Instruments[/b]"]
        for spec in INSTRUMENTS:
            name = escape(spec.short_label)
            if spec.key in app.handles:
                lines.append(f"[green]●[/green] {name}")
            else:
                lines.append(f"○ {name}")
        self.query_one("#nav-instruments", Static).update("\n".join(lines))


class IyzeeApp(App):
    """Connect to lab instruments, browse traces, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"
    CSS_PATH = "app.tcss"

    # Width breakpoints. Textual adds exactly one of these class names to
    # the Screen, so app.tcss can reflow the whole UI declaratively with
    # selectors like ``.-narrow #sweep-form`` — no resize handlers, no
    # layout code in the screens. The thresholds are terminal columns
    # (nav rail included), chosen so that at each tier the page area next
    # to the rail still fits that tier's densest row: three buttons side
    # by side from ``-medium`` up, four form fields per row at ``-wide``.
    # At ``-narrow`` everything stacks into a single column, and whatever
    # still doesn't fit is reached with the pages' scrollbars.
    HORIZONTAL_BREAKPOINTS = [
        (0, "-narrow"),
        (76, "-medium"),
        (116, "-wide"),
    ]

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
    # "escape" and "j"/"k" specifically: Textual's own Input widget
    # doesn't bind Escape to anything, so it already bubbles here
    # untouched — this doesn't intercept a key that widget wanted. (The
    # console's terminal view does want Escape — it is IPython's vi-mode
    # key — and claims it itself, so it never reaches this binding.)
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
        # Quit is Ctrl+Q only, deliberately not a bare "q": one stray
        # keystroke shouldn't be able to shut down an app that is holding
        # live instruments. Textual already binds Ctrl+Q, but hidden; this
        # re-declares it so the footer actually tells people how to leave.
        Binding("ctrl+q", "quit", "Quit", priority=True),
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

    def __init__(
        self,
        *,
        console_history_file: str | Path | None = None,
        console_editing_mode: str = "vi",
    ) -> None:
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
        # Set once the app starts shutting down. Long-running workers poll
        # it (SweepScreen checks it after every step) so a sweep stops at
        # the next step boundary and releases its instrument lock, which
        # is what lets close_instruments() disconnect cleanly.
        self.shutdown_requested = threading.Event()
        # True while a sweep or a trace capture is running. ConnectScreen
        # refuses to disconnect instruments meanwhile (the run holds the
        # instrument locks, so a disconnect could only stall behind it).
        self.sweep_running = False
        # Passed straight through to IPythonSession (see ConsoleScreen.compose)
        # as its `history_file`. Defaults to `None` — `:memory:`, private,
        # nothing persisted — quite deliberately: every test in this
        # codebase constructs `IyzeeApp()` with no arguments, and picking
        # any default here other than "no persistence" would silently
        # reintroduce the shared-file test-suite hang `default_history_file`
        # documents. Real persistence is opt-in, from the real entry point
        # only (see `run()` below).
        self.console_history_file = console_history_file
        # IPython's own editing mode for the console: "vi" or "emacs".
        self.console_editing_mode = console_editing_mode
        # Set by ConsoleScreen; closed on exit (see on_unmount).
        self.console_session: IPythonSession | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app-body"):
            yield NavRail(id="nav-rail")
            with ContentSwitcher(initial=self.DEFAULT_PAGE, id="page-switcher"):
                for page_id, page_cls in self.PAGES.items():
                    yield page_cls(id=page_id)
        yield Footer(compact=True)

    def action_show_page(self, page_id: str) -> None:
        self.query_one(ContentSwitcher).current = page_id
        nav = self.query_one(NavRail)
        nav.set_active(page_id)
        nav.refresh_instruments()
        # Which page-switch entries the footer offers depends on the page.
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Don't offer to navigate to the page you're already on.

        Returning False hides the footer entry (and the key does nothing,
        which is right: there's nowhere to go). It frees a slot on every
        page, which is what keeps the console's footer — the most crowded
        one — from clipping at 80 columns.
        """
        if action == "show_page" and parameters:
            switcher = self.query(ContentSwitcher)
            if switcher and parameters[0] == switcher.first().current:
                return False
        return True

    def instruments_changed(self) -> None:
        """Something connected or disconnected: refresh everything that shows it."""
        for nav in self.query(NavRail):
            nav.refresh_instruments()
        for sweep in self.query(SweepScreen):
            sweep.refresh_readiness()

    async def on_unmount(self) -> None:
        """Runs on every way out (Ctrl+Q, ``exit()``, test teardown).

        Without this, quitting simply dropped the process with every
        connected instrument still open — VISA sessions, the PSU channel
        driving the shutter, the scope socket. Off the UI thread, because
        a disconnect is blocking I/O and a wedged instrument must not be
        able to hang the quit.
        """
        self.shutdown_requested.set()
        await asyncio.to_thread(self._close_console_and_instruments)

    def _close_console_and_instruments(self) -> None:
        # The console first: a cell still running holds instrument locks, and
        # closing the session interrupts it so close_instruments can get them.
        if self.console_session is not None:
            self.console_session.close()
        self.close_instruments()

    def close_instruments(self, timeout: float = 5.0) -> None:
        """Disconnect every connected instrument, in parallel, within ``timeout``.

        Each disconnect takes that instrument's lock first, so it waits
        for an in-flight sweep step or console call instead of tearing the
        link down under it. An instrument that stays busy past the
        deadline is skipped (and logged) rather than blocking the exit —
        the OS reclaims its sockets when the process ends anyway.
        """
        handles = dict(self.handles)
        self.handles.clear()
        if not handles:
            return
        deadline = time.monotonic() + timeout

        def close(key: str, handle: InstrumentHandle) -> None:
            lock = self.instrument_locks[key]
            if not lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                log.warning("shutdown: %s still busy after %.0fs, not disconnecting", key, timeout)
                return
            try:
                handle.disconnect()
            except Exception:  # noqa: BLE001 - one bad instrument mustn't stop the rest
                log.exception("shutdown: error closing %s", key)
            finally:
                lock.release()

        threads = [
            threading.Thread(target=close, args=item, name=f"close-{item[0]}", daemon=True)
            for item in handles.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

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
    """Console-script entry point (``iyzee-tui``).

    The only place that opts into persistent console history — see
    `IyzeeApp.__init__` and `default_history_file`'s docstrings for why
    that's deliberately not the default.
    """
    IyzeeApp(
        console_history_file=default_history_file(),
        console_editing_mode=os.environ.get("IYZEE_EDITING_MODE", "vi"),
    ).run()


if __name__ == "__main__":
    run()
