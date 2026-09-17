"""Embedded IPython execution for the iyzee TUI."""

from __future__ import annotations

import atexit
import contextlib
import io
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol
from weakref import WeakSet

from IPython.core.interactiveshell import InteractiveShell
from traitlets.config import Config

from .instruments import LockedProxy

if TYPE_CHECKING:
    from .workers import LastRun


_IDENTIFIER_RE = re.compile(r"[\w.]*$")


def _identifier_before(source: str, cursor_pos: int) -> str:
    """The dotted identifier ending at ``cursor_pos``.

    E.g. ``"lab.mx"`` out of ``"lab.mx.freq()"`` at position 6. Matches
    IPython's own notion of a completable/inspectable token: word
    characters and dots, stopping at the first character that's neither
    (an open paren, a space, a comma, ...).
    """
    match = _IDENTIFIER_RE.search(source[:cursor_pos])
    return match.group(0) if match else ""


class AppState(Protocol):
    """What :class:`LabProxy` needs from the running app.

    A structural (duck-typed) view rather than importing ``IyzeeApp``
    directly — ``app.py`` imports the console screen, which imports this
    module, so importing ``IyzeeApp`` back here would be a cycle. Anything
    with these three attributes works, which is also what makes this easy
    to unit test with a small stand-in instead of a full running app.

    Declared as read-only ``@property`` members rather than plain
    attributes: ``LabProxy``/``IyzeeIPython`` only ever read through this
    protocol (``.get()``, indexing, ``in``), never assign a whole new
    mapping. Plain (writable) Protocol attributes are checked invariantly
    by mypy, which would reject ``IyzeeApp``'s actual ``dict[str, ...]``
    fields as not being exactly ``Mapping[str, ...]``; read-only
    properties are checked covariantly instead, so a ``dict`` (a
    ``Mapping`` subtype) satisfies this protocol as intended.
    """

    @property
    def handles(self) -> Mapping[str, Any]: ...

    @property
    def instrument_locks(self) -> Mapping[str, threading.Lock]: ...

    @property
    def last_run(self) -> LastRun | None: ...


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    """Captured result of one IPython cell execution."""

    source: str
    stdout: str
    stderr: str
    execution_count: int
    success: bool


class LabProxy:
    """Live view of the app's connected instruments and last sweep.

    Exposed to the console as a single ``lab`` variable
    (``lab.mx``, ``lab.shutter``, ``lab.scope``, ``lab.results``,
    ``lab.last_run``) rather than as separate top-level globals. Every
    attribute access does a fresh lookup against the running app's
    current state; nothing is ever copied or cached here, which is what
    makes this simpler and more robust than the namespace-copying
    approach it replaced:

    - **Nothing to refresh.** The old design copied live objects into the
      shell's namespace on connect and had to notice and clean them up
      again on disconnect (an explicit ``LIVE_NAMES`` allow-list, checked
      on every screen visit). ``lab`` needs none of that — disconnect the
      MXA and the very next ``lab.mx`` access reflects it immediately,
      because it re-reads ``app.handles`` every time rather than trusting
      a value captured earlier.
    - **No stale references.** With the old design, a disconnected
      instrument's copied handle kept working (badly) until the next
      refresh happened to notice it was gone. Here there's no copy to go
      stale in the first place — you get a clear ``AttributeError`` the
      moment the thing you're asking for isn't there, immediately, not on
      some delay tied to screen navigation.
    - **No name-collision risk.** The old design owned five bare names in
      the shell (``mx``, ``shutter``, ``scope``, ``results``,
      ``handles``) that a user's own variable of the same name could
      collide with. ``lab`` owns exactly one name; shadow ``mx`` for a
      scratch calculation and it's a completely ordinary Python variable
      with zero interaction with app state, forever.

    Add a new instrument by adding one entry to ``_INSTRUMENT_KEYS``
    below (attribute name -> ``app.handles``/``app.instrument_locks``
    key) — nothing else needs to change.
    """

    _INSTRUMENT_KEYS = {"mx": "mxa", "shutter": "shutter", "scope": "scope"}

    def __init__(self, app: AppState) -> None:
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name: str) -> Any:
        app = object.__getattribute__(self, "_app")
        instrument_keys = object.__getattribute__(self, "_INSTRUMENT_KEYS")
        if name in instrument_keys:
            key = instrument_keys[name]
            handle = app.handles.get(key)
            if handle is None:
                raise AttributeError(
                    f"lab.{name} is not connected — connect it on the Connect screen first"
                )
            device = _device_from_handle(name, handle)
            return LockedProxy(device, app.instrument_locks[key])
        if name == "results":
            return app.last_run.results if app.last_run is not None else []
        if name == "last_run":
            return app.last_run
        if name == "handles":
            return app.handles
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("lab is read-only — assign to a new variable instead")

    def __dir__(self) -> list[str]:
        # Always lists the full set regardless of what's connected right
        # now, so `lab.<Tab>` in the console shows what's *possible*
        # (mx/shutter/scope), not just what happens to be connected at
        # this exact moment.
        return sorted({*self._INSTRUMENT_KEYS, "results", "last_run", "handles", "connected"})

    @property
    def connected(self) -> tuple[str, ...]:
        """Names of the instruments currently connected, e.g. ``("mx",)``."""
        app = object.__getattribute__(self, "_app")
        return tuple(name for name, key in self._INSTRUMENT_KEYS.items() if key in app.handles)


def _device_from_handle(name: str, handle: Any) -> Any:
    """Unwrap an ``InstrumentHandle`` to the underlying live driver object.

    Mirrors each handle's own accessor in ``instruments.py``
    (``_VisaHandle.device``, ``ShutterHandle.shutter``,
    ``ScopeHandle.scope``) — different names because the underlying
    drivers themselves are heterogeneous (VISA vs. a PSU wrapper vs. a
    raw-socket driver), not by accident.
    """
    if name == "mx":
        return getattr(handle, "device", handle)
    if name == "shutter":
        live = getattr(handle, "shutter", None)
        return live if live is not None else handle
    if name == "scope":
        return getattr(handle, "scope", handle)
    return handle


class IyzeeIPython:
    """Own one embedded :class:`InteractiveShell` for the running TUI."""

    _instances: ClassVar[WeakSet[IyzeeIPython]] = WeakSet()

    def __init__(self, app: AppState, *, namespace: Mapping[str, Any] | None = None) -> None:
        config = Config()
        config.InteractiveShell.automagic = True
        config.InteractiveShell.autoawait = True
        config.InteractiveShell.autoindent = True
        config.InteractiveShell.display_page = True
        config.InteractiveShell.history_load_length = 0
        config.InteractiveShell.log_output = False
        config.InteractiveShell.banner1 = ""
        config.InteractiveShell.banner2 = ""
        # Jedi does static analysis and can't see through LabProxy's
        # dynamic __getattr__ — with it on, `lab.<Tab>` silently returns
        # no completions at all (checked directly: Jedi returns nothing
        # for `lab.` and IPython's complete() does not fall back to its
        # own simpler dir()-based completer when that happens). Regular
        # attribute/module/keyword completion elsewhere is unaffected.
        config.Completer.use_jedi = False
        user_ns = dict(namespace or {})
        # Set once, never reassigned — see LabProxy's docstring for why
        # this replaces refreshing a set of copied globals on every visit.
        user_ns["lab"] = LabProxy(app)
        self.shell = InteractiveShell(config=config, user_ns=user_ns)
        self.shell.init_completer()
        # IPython's own internal helper `get_ipython()` (used by page(), the
        # pager routing behind `?`/`??`, %pdoc, ...) does not resolve to
        # `self.shell` just because we constructed it — that helper reads
        # InteractiveShell's class-level singleton pointer, which the plain
        # constructor never sets. Left unset, `page()` sees no active shell,
        # skips our `display_page=True` hook entirely, and falls through to
        # the real interactive pager — which blocks on real stdin for
        # "Return to continue" and immediately hits EOFError, since Textual
        # owns the terminal. Assigning the class attribute directly (instead
        # of using `InteractiveShell.instance()`, which would silently
        # *reuse* an existing shell rather than build this new, independent
        # one — see test_history_does_not_leak_across_shell_instances)
        # activates this shell for `get_ipython()` without weakening the
        # isolation between separate `IyzeeIPython` instances.
        InteractiveShell._instance = self.shell
        self._lock = threading.RLock()
        self._closed = False
        self._instances.add(self)

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
        """Return IPython's object inspection payload for the identifier at ``cursor_pos``.

        ``InteractiveShell.object_inspect`` takes an object *name*, not a
        source string plus a cursor position — it has no ``cursor_pos``
        parameter at all. The previous implementation passed ``cursor_pos``
        positionally into ``object_inspect``'s ``detail_level`` slot and
        then passed ``detail_level`` again by keyword, which raised
        ``TypeError: object_inspect() got multiple values for argument
        'detail_level'`` on every call. This extracts the dotted
        identifier ending at ``cursor_pos`` (e.g. ``"lab.mx"`` out of
        ``"lab.mx.freq"`` at position 6) and inspects that instead.
        """
        with self._lock:
            oname = _identifier_before(source, cursor_pos)
            return self.shell.object_inspect(oname, detail_level=detail_level)

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
            # traitlets' Instance descriptor makes this Any | None to
            # mypy; IPython's own source (interactiveshell.py) asserts
            # the same thing before use rather than narrowing the
            # declared type, so this follows that convention.
            assert self.shell.history_manager is not None
            for _session, _line, cell in self.shell.history_manager.get_range(session=0, raw=True):
                cell = cell.rstrip()
                if cell and cell != last:
                    entries.append(cell)
                    last = cell
            return entries

    def close(self) -> None:
        """Shut down this embedded shell and unregister its process exit hook."""
        with self._lock:
            if self._closed:
                return
            atexit.unregister(self.shell.atexit_operations)
            self.shell.atexit_operations()
            self._closed = True
            self._instances.discard(self)
            if InteractiveShell._instance is self.shell:
                InteractiveShell._instance = None
