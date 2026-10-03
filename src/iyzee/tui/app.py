"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.markup import escape
from textual.widgets import ContentSwitcher, Footer, Header, Static

from .commands import CommandBar, CommandError, help_text, parse_command
from .instruments import INSTRUMENTS, InstrumentHandle
from .ipython import default_history_file
from .ipython_session import IPythonSession
from .logging_support import LogEntry
from .logging_support import install as install_logging
from .prefs import Prefs, default_prefs_file
from .screens.connect import ConnectScreen
from .screens.console import ConsoleScreen
from .screens.log import LogScreen
from .screens.page import Page
from .screens.results import ResultsScreen
from .screens.scope import ScopeScreen
from .screens.sweep import SweepScreen
from .commands import CommandBar, CommandError, parse_command
from .workers import LastRun

log = logging.getLogger("iyzee.tui")


PAGE_SPECS = (
    ("connect", "Connect", ConnectScreen),
    ("sweep", "Sweep", SweepScreen),
    ("scope", "Scope", ScopeScreen),
    ("results", "Results", ResultsScreen),
    ("console", "Console", ConsoleScreen),
    ("log", "Activity", LogScreen),
)


class NavRail(Static):
    """Persistent navigation grouped by the user's current task.

    The rail describes work rather than implementation: hardware, acquisition,
    analysis, and tools. Instrument state remains here as ambient lab context.
    """

    SECTIONS = (
        ("LAB", ("connect",)),
        ("ACQUIRE", ("sweep", "scope")),
        ("ANALYZE", ("results",)),
        ("TOOLS", ("console", "log")),
    )

    PAGES = tuple((page_id, label) for page_id, label, _screen in PAGE_SPECS)
    LABELS = dict(PAGES)

    def compose(self) -> ComposeResult:
        yield Static("iyzee", id="nav-title")
        for section, page_ids in self.SECTIONS:
            yield Static(section, classes="nav-section")
            for page_id in page_ids:
                yield Static(
                    self.LABELS[page_id],
                    id=f"nav-{page_id}",
                    classes="nav-item",
                )
        yield Static("", id="nav-instruments")

    def on_mount(self) -> None:
        self.set_active("connect")
        self.refresh_instruments()

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None or not widget.has_class("nav-item") or widget.id is None:
            return
        cast("IyzeeApp", self.app).action_show_page(widget.id.removeprefix("nav-"))

    def set_active(self, page_id: str) -> None:
        for pid, _ in self.PAGES:
            self.query_one(f"#nav-{pid}", Static).set_class(pid == page_id, "-active")

    def refresh_instruments(self) -> None:
        """Update the ambient instrument status block."""
        app = cast("IyzeeApp", self.app)
        lines = ["[b]Instruments[/b]"]
        for spec in INSTRUMENTS:
            name = escape(spec.short or spec.label)
            lines.append(
                f"[green]●[/green] {name}" if spec.key in app.handles else f"○ {name}"
            )
        self.query_one("#nav-instruments", Static).update("\n".join(lines))


class IyzeeApp(App):
    """Connect to lab instruments, browse results, and use IPython."""

    TITLE = "iyzee"
    SUB_TITLE = "lab instrument control"

    # Seconds between idle-link checks (see ``_check_links``).
    LINK_CHECK_INTERVAL = 2.0
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

    # Keep Textual's built-in palette away from IPython's Ctrl+P history key.
    # The application command layer is opened with ":" or Ctrl+\.
    COMMAND_PALETTE_BINDING = "ctrl+shift+space"


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
    # "o" for the Scope page joins the letter shortcuts (c/s/t/i) rather
    # than an F-key: F1-F4 are specifically special-cased elsewhere —
    # terminal_view._APP_KEYS lets exactly those four bypass the console's
    # embedded terminal and reach the app, and both the in-app help text
    # and ipython_session's banner document "F1-F4 switch pages". Reusing
    # one of those four for Scope would silently break whichever page it
    # displaced when a console cell has focus; adding a fifth would not
    # reach the app at all from inside the terminal (F5+ are CSI-tilde
    # sequences, a different wire format _APP_KEYS/termkeys.py don't
    # handle — see termkeys.py's own comment on this split). So Scope is
    # reachable everywhere via "o", the nav rail, and the command line,
    # just without a dedicated function key. "l" for the Log page (added
    # later) follows the same reasoning.
    BINDINGS = [
        Binding("c", "show_page('connect')", "Connect"),
        Binding("s", "show_page('sweep')", "Sweep"),
        Binding("o", "show_page('scope')", "Scope"),
        Binding("t", "show_page('results')", "Results"),
        Binding("i", "show_page('console')", "Console"),
        Binding("l", "show_page('log')", "Activity"),
        Binding("f1", "show_page('connect')", "Connect", priority=True),
        Binding("f2", "show_page('sweep')", "Sweep", priority=True),
        Binding("f3", "show_page('results')", "Results", priority=True),
        Binding("f4", "show_page('console')", "Console", priority=True),
        # Ctrl+Q quits at once (Textual already binds it, but hidden; this
        # re-declares it so the footer tells people how to leave). A bare "q"
        # also quits, but only on a second press: one stray keystroke
        # shouldn't shut down an app that is holding live instruments. Hidden
        # from the footer, which is already crowded; the first press says so.
        Binding("ctrl+backslash", "open_command", "Command", show=False, priority=True),
        Binding(":", "open_command", "Command", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("q", "request_quit", "Quit", show=False),
        Binding("escape", "blur_focused", "Leave field", show=False),
        Binding("j", "focus_next", "Focus next", show=False),
        Binding("k", "focus_previous", "Focus previous", show=False),
    ]

    # One page widget per entry, held all at once inside a ContentSwitcher
    # instead of the old App.MODES (a separate, independently-mounted
    # Screen per page). This is what makes a persistent NavRail possible —
    # MODES/switch_mode() tears down and rebuilds the whole screen on every
    # switch, so nothing outside the switched screen could ever persist.
    # Mount every page once; navigation labels come from the same registry
    # used by NavRail.PAGES above. Key bindings remain explicit because F1-F4
    # intentionally cover only four pages.
    PAGES = {page_id: screen for page_id, _label, screen in PAGE_SPECS}
    DEFAULT_PAGE = "connect"

    def __init__(
        self,
        *,
        console_history_file: str | Path | None = None,
        console_editing_mode: str = "vi",
        prefs_file: str | Path | None = None,
    ) -> None:
        super().__init__()
        # Like the console history, persistent only for real runs (see ``run()``).
        self.prefs = Prefs(Path(prefs_file) if prefs_file else None)
        self._quit_armed_until = 0.0  # monotonic deadline for the second "q"
        # Installed first, before anything else in this constructor can
        # fail: this is the sink for every log.exception/log.warning/
        # log.info call anywhere in the app (see logging_support's module
        # docstring for why nothing showed any of this before). Kept as
        # self.log_handler, not a module global, so LogScreen reads the
        # live buffer straight off the running app rather than needing its
        # own reference to a handler that (in tests) gets replaced every
        # time a new IyzeeApp() is constructed — see install()'s docstring.
        self.log_handler = install_logging(self._on_log_entry)
        self.handles: dict[str, InstrumentHandle] = {}
        # No per-app lock table anymore — each InstrumentHandle owns its
        # own threading.Lock (see instruments.InstrumentHandle.lock) so
        # that a page's background worker (Connect, Sweep), the IPython
        # console (via LockedProxy — see ipython.namespace_from_handles),
        # and a handle used entirely outside this app all get the same
        # correct synchronization for free, rather than it only existing
        # because IyzeeApp happens to be running.
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
        # Instruments whose link died while connected: key -> why. A lost link
        # is removed from ``handles`` (so "in handles" keeps meaning "usable"
        # for the nav rail, readiness banners and the console alike) and
        # remembered here so the Connect page can say what happened.
        self.lost_links: dict[str, str] = {}

    def on_mount(self) -> None:
        self.set_interval(self.LINK_CHECK_INTERVAL, self._check_links)

    def _check_links(self) -> None:
        """Notice links that died while nobody was talking to them.

        Cheap: ``alive`` does no instrument I/O. Handles without the
        attribute (test doubles, adapters that cannot tell) count as alive.
        """
        for key, handle in list(self.handles.items()):
            if not getattr(handle, "alive", True):
                self._link_lost(key, handle)

    def _link_lost(self, key: str, handle: InstrumentHandle) -> None:
        if self.handles.get(key) is not handle:
            return  # already disconnected or replaced
        del self.handles[key]
        spec = next((s for s in INSTRUMENTS if s.key == key), None)
        label = spec.label if spec is not None else key
        reason = "link lost - press Enter to reconnect"
        self.lost_links[key] = reason
        log.warning("%s: connection lost; dropped it so it can be reconnected", key)
        # Release whatever is left of the dead link off the UI thread: the
        # driver's disconnect takes the instrument lock.
        threading.Thread(
            target=self._release_dead_link, args=(key, handle), name=f"release-{key}", daemon=True
        ).start()
        for screen in self.query(ConnectScreen):
            screen.show_lost(key, reason)
        self.instruments_changed()
        self.notify(
            f"{label}: connection lost. Reconnect it on the Connect page.",
            severity="error",
            timeout=10,
            markup=False,
        )

    @staticmethod
    def _release_dead_link(key: str, handle: InstrumentHandle) -> None:
        try:
            with handle.lock:
                handle.disconnect()
        except Exception:  # noqa: BLE001 - the link is already dead; just don't leak
            log.debug("releasing dead link %s failed", key, exc_info=True)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app-body"):
            yield NavRail(id="nav-rail")
            with ContentSwitcher(initial=self.DEFAULT_PAGE, id="page-switcher"):
                for page_id, page_cls in self.PAGES.items():
                    yield page_cls(id=page_id)
        yield Footer(compact=True)
        yield CommandBar(id="command-bar")

    # -- command line ------------------------------------------------------

    def action_open_command(self) -> None:
        switcher = self.query_one(ContentSwitcher)
        self.query_one(CommandBar).open(switcher.current or self.DEFAULT_PAGE)

    def action_close_command(self) -> None:
        self.query_one(CommandBar).close()

    def execute_command(self, value: str) -> str | None:
        """Execute one parsed command, using the existing screen action paths."""
        tokens = parse_command(value)
        if not tokens:
            return None

        command, *args = tokens
        command = command.lower()
        instrument_keys = {spec.key for spec in INSTRUMENTS}

        if command in {"connect", "c"}:
            if not args:
                self.action_show_page("connect")
                return None
            if len(args) != 1 or args[0].lower() not in instrument_keys:
                raise CommandError(
                    f"unknown instrument {args[0]!r}; choose from {', '.join(sorted(instrument_keys))}"
                )
            self.query_one(ConnectScreen).command_connect(args[0].lower())
            return None

        if command in {"disconnect", "dc"}:
            if len(args) != 1 or args[0].lower() not in instrument_keys:
                raise CommandError("usage: :disconnect <instrument>")
            return self.query_one(ConnectScreen).command_disconnect(args[0].lower())

        if command in {"sweep", "s"}:
            if not args:
                self.action_show_page("sweep")
                return None
            if len(args) != 1 or args[0].lower() not in {"run", "abort", "capture"}:
                raise CommandError("usage: :sweep [run|abort|capture]")
            sweep = self.query_one(SweepScreen)
            action = args[0].lower()
            if action == "run":
                sweep.command_run()
            elif action == "abort":
                sweep.command_abort()
            else:
                sweep.command_capture()
            return None

        if command in {"scope", "o"}:
            if not args:
                self.action_show_page("scope")
                return None
            if len(args) != 1 or args[0].lower() not in {"sync", "acquire"}:
                raise CommandError("usage: :scope [sync|acquire]")
            scope = self.query_one(ScopeScreen)
            if args[0].lower() == "sync":
                scope.command_sync()
            else:
                scope.command_acquire()
            return None

        if command in {"results", "result", "t"}:
            if args:
                raise CommandError("usage: :results")
            self.action_show_page("results")
            return None

        if command in {"console", "i"}:
            if args:
                raise CommandError("usage: :console")
            self.action_show_page("console")
            return None

        if command in {"activity", "log", "l"}:
            if args:
                raise CommandError("usage: :activity")
            self.action_show_page("log")
            return None

        if command in {"help", "?"}:
            return help_text()

        if command in {"quit", "q", "exit"}:
            if args:
                raise CommandError("usage: :quit")
            self.action_request_quit()
            return None

        raise CommandError(f"unknown command: :{command} — try :help")


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
        """Refresh every mounted view whose state depends on connections."""
        for nav in self.query(NavRail):
            nav.refresh_instruments()
        for page in self.query(Page):
            page.refresh_readiness()

    def _on_log_entry(self, entry: LogEntry) -> None:
        """``TuiLogHandler``'s callback — runs on whichever thread just
        logged something, which is almost never this app's own thread (see
        ``TuiLogHandler``'s docstring), so hop onto it before touching any
        widget, the same as every screen's own worker->UI callbacks do.

        ``call_from_thread`` itself refuses to run when it's *already* on
        the app's thread (a direct call, not from a worker) as well as
        when the app isn't running yet (a log call during startup, before
        ``run()``) — both are real, both just mean "call it directly"
        rather than an error worth surfacing.
        """
        try:
            self.call_from_thread(self._show_log_entry, entry)
        except RuntimeError:
            self._show_log_entry(entry)

    def _show_log_entry(self, entry: LogEntry) -> None:
        for screen in self.query(LogScreen):
            screen.append_entry(entry)

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
            lock = handle.lock
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

    QUIT_CONFIRM_S = 4.0

    def action_request_quit(self) -> None:
        """``q``: the first press asks, a second one within a few seconds quits."""
        now = time.monotonic()
        if now <= self._quit_armed_until:
            self.exit()
            return
        self._quit_armed_until = now + self.QUIT_CONFIRM_S
        connected = len(self.handles)
        extra = f" {connected} connected instrument(s) will be disconnected." if connected else ""
        self.notify(
            f"Press q again to quit.{extra}",
            severity="warning",
            timeout=self.QUIT_CONFIRM_S,
            markup=False,
        )

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
        prefs_file=default_prefs_file(),
    ).run()


if __name__ == "__main__":
    run()
