"""Textual application entry point for the iyzee lab-control TUI."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import ClassVar, cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.markup import escape
from textual.widgets import ContentSwitcher, Footer, Header, Static

from ..devices.handles import InstrumentHandle
from ..lab import INSTRUMENTS, Lab
from .logging_support import LogEntry
from .logging_support import install as install_logging
from .screens.connect import ConnectScreen
from .screens.log import LogScreen
from .screens.page import Page
from .screens.power import PowerScreen
from .screens.results import ResultsScreen
from .screens.scope import ScopeScreen
from .screens.sweep import SweepScreen
from .workers import LastRun

log = logging.getLogger("iyzee.tui")


PAGE_SPECS = (
    ("connect", "Connect", ConnectScreen),
    ("sweep", "Sweep", SweepScreen),
    ("scope", "Scope", ScopeScreen),
    ("power", "Power", PowerScreen),
    ("results", "Results", ResultsScreen),
    ("log", "Log", LogScreen),
)


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

    # Page ids, labels, and widget classes share one registry so adding a
    # page cannot leave the nav rail and ContentSwitcher out of sync.
    PAGES = tuple((page_id, label) for page_id, label, _screen in PAGE_SPECS)

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
            name = escape(spec.short or spec.label)
            if spec.key in app.handles:
                lines.append(f"[green]●[/green] {name}")
            else:
                lines.append(f"○ {name}")
        self.query_one("#nav-instruments", Static).update("\n".join(lines))


class IyzeeApp(App):
    """Connect to lab instruments, run sweeps, and browse results."""

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
    HORIZONTAL_BREAKPOINTS: list[tuple[int, str]] = [  # noqa: RUF012 - Textual class configuration
        (0, "-narrow"),
        (76, "-medium"),
        (116, "-wide"),
    ]

    # Navigation lives in Textual bindings (not ``on_key``) so editable widgets keep their own
    # key handling and the Footer shows the same keys. Plain "j"/"k" only fire when no widget
    # is consuming the character.
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("c", "show_page('connect')", "Connect"),
        Binding("s", "show_page('sweep')", "Sweep"),
        Binding("o", "show_page('scope')", "Scope"),
        Binding("p", "show_page('power')", "Power"),
        Binding("t", "show_page('results')", "Results"),
        Binding("l", "show_page('log')", "Log"),
        Binding("f1", "show_page('connect')", "Connect", priority=True),
        Binding("f2", "show_page('sweep')", "Sweep", priority=True),
        Binding("f3", "show_page('results')", "Results", priority=True),
        # Ctrl+Q quits at once (Textual already binds it, but hidden; this
        # re-declares it so the footer tells people how to leave). A bare "q"
        # also quits, but only on a second press: one stray keystroke
        # shouldn't shut down an app that is holding live instruments. Hidden
        # from the footer, which is already crowded; the first press says so.
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
    PAGES: ClassVar[dict[str, type[Page]]] = {
        page_id: cast(type[Page], screen) for page_id, _label, screen in PAGE_SPECS
    }
    DEFAULT_PAGE = "connect"

    def __init__(self) -> None:
        super().__init__()
        self._quit_armed_until = 0.0  # monotonic deadline for the second "q"
        # Sink for every log call in the app; LogScreen reads the live buffer off it.
        self.log_handler = install_logging(self._on_log_entry)
        # The session's instruments live in the core ``Lab`` (no Textual in it);
        # ``handles`` is its connected-handles dict, shared rather than copied.
        self.lab = Lab()
        self.handles: dict[str, InstrumentHandle] = self.lab.handles
        # The most recently completed sweep, if any (set by SweepScreen._finish).
        self.last_run: LastRun | None = None
        # Set once the app starts shutting down. Long-running workers poll it (a sweep
        # checks it after every step) so they stop and release their instrument lock.
        self.shutdown_requested = threading.Event()
        # True while a sweep or a trace capture is running. ConnectScreen refuses to
        # disconnect instruments meanwhile (the run holds the instrument locks).
        self._sweep_running = False
        # Instruments whose link died while connected: key -> why. A lost link is removed
        # from ``handles`` and remembered here so the Connect page can say what happened.
        self.lost_links: dict[str, str] = {}

    def on_mount(self) -> None:
        self.set_interval(self.LINK_CHECK_INTERVAL, self._check_links)

    def _check_links(self) -> None:
        """Notice links that died while nobody was talking to them (no instrument I/O)."""
        for key in self.lab.drop_dead_links():
            self._link_lost(key)

    def _link_lost(self, key: str) -> None:
        spec = next((s for s in INSTRUMENTS if s.key == key), None)
        label = spec.label if spec is not None else key
        reason = "link lost - press Enter to reconnect"
        self.lost_links[key] = reason
        for screen in self.query(ConnectScreen):
            screen.show_lost(key, reason)
        self.instruments_changed()
        self.notify(
            f"{label}: connection lost. Reconnect it on the Connect page.",
            severity="error",
            timeout=10,
            markup=False,
        )

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
        page = cast("Page", self.query_one(f"#{page_id}"))
        page.on_show()
        nav = self.query_one(NavRail)
        nav.set_active(page_id)
        nav.refresh_instruments()
        # Which page-switch entries the footer offers depends on the page.
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Don't offer to navigate to the page you're already on.

        Returning False hides the footer entry (and the key does nothing,
        which is right: there's nowhere to go). It frees a slot on every
        page, which keeps the footer from clipping at 80 columns.
        """
        if action == "show_page" and parameters:
            switcher = self.query(ContentSwitcher)
            if switcher and parameters[0] == switcher.first().current:
                return False
        return True

    @property
    def sweep_running(self) -> bool:
        return self._sweep_running

    @sweep_running.setter
    def sweep_running(self, running: bool) -> None:
        """Set by the Sweep page; the Power page follows it (a sweep moves the shutter)."""
        changed = running != self._sweep_running
        self._sweep_running = running
        if changed and self.is_running:
            for page in self.query(PowerScreen):
                page.sweep_state_changed()

    def instruments_changed(self) -> None:
        """Refresh every mounted view whose state depends on connections."""
        for nav in self.query(NavRail):
            nav.refresh_instruments()
        for page in self.query(Page):
            page.refresh_readiness()

    def _on_log_entry(self, entry: LogEntry) -> None:
        """Update the live log view on the UI thread while the app is running."""
        if not self.is_running:
            return
        if threading.get_ident() == self._thread_id:
            self._show_log_entry(entry)
        else:
            with contextlib.suppress(RuntimeError):
                self.call_from_thread(self._show_log_entry, entry)

    def _show_log_entry(self, entry: LogEntry) -> None:
        for screen in self.query(LogScreen):
            screen.append_entry(entry)

    def on_unmount(self) -> None:
        """Runs on every way out (Ctrl+Q, ``exit()``, test teardown).

        Without this, quitting dropped the process with every instrument still open: VISA
        sessions, the PSU channel driving the shutter, the scope socket. ``Lab.close_all``
        takes each instrument's lock, so it waits for an in-flight step and gives up after
        its timeout rather than letting a wedged instrument hang the quit.
        """
        self.shutdown_requested.set()
        self.lab.close_all(5.0)

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
        plain Input fields (RBW, IP addresses, ...): those widgets have no notion of their
        own to leave, so without this, focusing one meant "j"/"k" typed into it forever
        instead of moving focus.
        """
        if self.focused is not None:
            self.focused.blur()


def run() -> None:
    """Console-script entry point (``iyz``)."""
    IyzeeApp().run()


if __name__ == "__main__":
    run()
