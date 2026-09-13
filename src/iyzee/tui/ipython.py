"""Embedded IPython execution for the iyzee TUI."""

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from IPython.core.interactiveshell import InteractiveShell
from traitlets.config import Config

from .instruments import LockedProxy


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    """Captured result of one IPython cell execution."""

    source: str
    stdout: str
    stderr: str
    execution_count: int
    success: bool


class IyzeeIPython:
    """Own one embedded :class:`InteractiveShell` for the running TUI."""

    # The complete set of names this class manages on the app's behalf —
    # currently the live devices (namespace_from_handles) plus the last
    # completed sweep (ConsoleScreen._namespace). When one of these drops
    # out of a call to update_namespace() (e.g. an instrument was
    # disconnected), it is actively removed from the shell rather than
    # left pointing at stale state — see update_namespace().
    #
    # This has to be an explicit set, not something derived purely from
    # "whatever was passed to update_namespace() last time": a value can
    # arrive via this same mechanism without being ours to manage — e.g.
    # a test (or, in principle, a future caller) seeding IyzeeIPython's
    # *constructor* with both an app-owned name and a user-style variable
    # in the same dict. A tracker with no notion of which names are ours
    # cannot tell those apart, and will delete the user's variable the
    # next time the app-owned name disappears. Add a new managed name
    # here when you add one to namespace_from_handles() or
    # ConsoleScreen._namespace() — nothing else needs to change.
    LIVE_NAMES = frozenset(("mx", "shutter", "scope", "handles", "results", "last_run"))

    def __init__(self, namespace: Mapping[str, Any] | None = None) -> None:
        config = Config()
        config.InteractiveShell.automagic = True
        config.InteractiveShell.autoawait = True
        config.InteractiveShell.autoindent = True
        config.InteractiveShell.display_page = True
        config.InteractiveShell.history_load_length = 0
        config.InteractiveShell.log_output = False
        config.InteractiveShell.banner1 = ""
        config.InteractiveShell.banner2 = ""
        self.shell = InteractiveShell(config=config, user_ns=dict(namespace or {}))
        self.shell.init_completer()
        self._lock = threading.RLock()

    def update_namespace(self, namespace: Mapping[str, Any]) -> None:
        """Refresh live application names and remove disconnected handles.

        Only names in :data:`LIVE_NAMES` are ever touched — anything else
        the caller passes in, or that the user assigned themselves during
        the session, is left alone. A name in ``LIVE_NAMES`` that is
        *absent* from this call's ``namespace`` (e.g. "mx" after the MXA
        was disconnected) is removed rather than left pointing at stale
        state, which would otherwise fail confusingly — not obviously —
        the next time someone used it from the console.
        """
        with self._lock:
            for name in self.LIVE_NAMES:
                if name in namespace:
                    self.shell.user_ns[name] = namespace[name]
                else:
                    self.shell.user_ns.pop(name, None)

    def execute(self, source: str) -> ExecutionOutput:
        """Execute one cell and capture terminal-oriented output."""
        source = source.rstrip()
        if not source.strip():
            return ExecutionOutput(
                source="",
                stdout="",
                stderr="",
                execution_count=self.shell.execution_count,
                success=True,
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self._lock, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.shell.run_cell(source, store_history=True, silent=False)
        return ExecutionOutput(
            source=source,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            execution_count=self.shell.execution_count,
            success=result.error_in_exec is None and result.error_before_exec is None,
        )

    def complete(self, source: str, cursor_pos: int) -> tuple[str, list[str]]:
        """Return IPython's completion edit and candidate list."""
        with self._lock:
            return self.shell.complete(source, line=source, cursor_pos=cursor_pos)

    def inspect(self, source: str, cursor_pos: int, detail_level: int = 0) -> dict[str, Any]:
        """Return IPython's object inspection payload."""
        with self._lock:
            return self.shell.object_inspect(source, cursor_pos, detail_level=detail_level)

    @property
    def history(self) -> list[str]:
        """Return this session's input history, oldest first, deduped.

        Sourced from ``history_manager.get_range(session=0)`` —
        ``session=0`` is IPython's own convention for "the current
        session" (see ``HistoryManager.get_range``) — rather than
        ``get_tail()``, which is what IPython's terminal frontend uses
        for its persistent, cross-session up/down history (see
        ``IPython.terminal.interactiveshell.PtkHistoryAdapter``). That's
        deliberate: this app is a long-running process, not a
        short-lived shell, so mixing in commands from an unrelated
        earlier visit to this screen would be surprising rather than
        helpful the way cross-session recall is for a normal `ipython`
        session. Consecutive blank/duplicate entries are dropped, the
        same filtering ``PtkHistoryAdapter`` applies.
        """
        with self._lock:
            entries: list[str] = []
            last = ""
            for _session, _line, cell in self.shell.history_manager.get_range(session=0, raw=True):
                cell = cell.rstrip()
                if cell and cell != last:
                    entries.append(cell)
                    last = cell
            return entries

    def close(self) -> None:
        """Run IPython's shutdown hooks."""
        with self._lock:
            self.shell.atexit_operations()


def namespace_from_handles(
    handles: Mapping[str, Any], locks: Mapping[str, threading.Lock] | None = None
) -> dict[str, Any]:
    """Build the live-device namespace exposed to the console.

    When ``locks`` is given (normally ``IyzeeApp.instrument_locks``), each
    live device is wrapped in a :class:`~iyzee.tui.instruments.LockedProxy`
    keyed by the same instrument key a screen's background worker locks
    around its own hardware calls — see ``LockedProxy`` for why this
    matters. Omitting ``locks`` exposes the raw device objects instead,
    which is only appropriate for tests that don't touch real threads.
    """
    namespace: dict[str, Any] = {}
    mxa = handles.get("mxa")
    if mxa is not None:
        namespace["mx"] = _locked(getattr(mxa, "device", mxa), "mxa", locks)
    shutter = handles.get("shutter")
    if shutter is not None:
        live_shutter = getattr(shutter, "shutter", None)
        device = live_shutter if live_shutter is not None else shutter
        namespace["shutter"] = _locked(device, "shutter", locks)
    scope = handles.get("scope")
    if scope is not None:
        namespace["scope"] = _locked(getattr(scope, "scope", scope), "scope", locks)
    namespace["handles"] = handles
    return namespace


def _locked(device: Any, key: str, locks: Mapping[str, threading.Lock] | None) -> Any:
    if locks is None:
        return device
    return LockedProxy(device, locks[key])
