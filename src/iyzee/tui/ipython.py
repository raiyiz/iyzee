"""Embedded IPython execution for the iyzee TUI."""

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

from IPython.core.interactiveshell import InteractiveShell
from platformdirs import user_data_dir
from traitlets.config import Config

from .instruments import LockedProxy

if TYPE_CHECKING:
    from .workers import LastRun


def default_history_file() -> Path:
    """Where the console's persistent input history lives for real runs
    of the app — a per-user data directory (``platformdirs``), not
    IPython's own default (``~/.ipython/profile_default/history.sqlite``).

    That default matters: it's shared by *every* ``InteractiveShell`` on
    the machine, this app's included, and every one of them registers its
    own `atexit` hook against it — accumulate enough of those (a single
    test run alone constructs 100+) and shutdown ends up serializing
    against a file that never gets smaller, which is what one earlier
    version of this app's test suite hit as a very real, reproducible
    hang. A dedicated path for this app alone avoids sharing that fate
    with anything else IPython-based on the same machine, real or test.
    This function is only ever called from the real entry point
    (``iyzee.tui.app.run``) for exactly that reason — tests construct
    ``IyzeeApp``/``IyzeeIPython`` with no history file at all, which
    keeps them on ``:memory:`` (private, thrown away when the process
    exits) without needing to know this function exists.
    """
    return Path(user_data_dir("iyzee")) / "console_history.sqlite"


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


class _StreamTee:
    """A ``sys.stdout``/``sys.stderr`` replacement that forwards output
    live, one complete line at a time, while still buffering everything
    (so ``ExecutionOutput.stdout``/``.stderr`` keeps working exactly as
    before for callers — tests included — that only care about the final
    captured text and never pass a callback at all).

    Lines, not raw ``write()`` chunks: a single ``print("a", "b")`` call
    does several separate ``file.write()`` calls under the hood — one per
    argument plus the separator plus the trailing newline — so forwarding
    each chunk as its own unit of output would fragment one logical line
    of output into several as far as a line-oriented consumer (the
    console's ``RichLog``, one call to ``.write()`` per line) is
    concerned. This buffers until a newline shows up before calling back,
    and holds on to a trailing partial line (e.g. ``print("...", end="")``
    with no newline) until either the next newline arrives or
    ``finish_partial_line()`` is called explicitly once the cell is done.

    Everything besides ``write()``/``getvalue()`` (``flush()``,
    ``isatty()``, ...) delegates straight through to a real
    ``io.StringIO`` via ``__getattr__``, so this remains a drop-in
    replacement for the plain ``io.StringIO()`` ``execute()`` redirected
    to before — nothing here should notice or care that IPython's own
    machinery treats ``sys.stdout``/``sys.stderr`` as an ordinary file.
    """

    def __init__(self, on_line: Callable[[str], None] | None = None) -> None:
        self._buffer = io.StringIO()
        self._on_line = on_line
        self._partial = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buffer.write(s)
        if self._on_line is not None:
            *complete, self._partial = (self._partial + s).split("\n")
            for line in complete:
                self._on_line(line)
        return len(s)

    def finish_partial_line(self) -> None:
        """Flush a trailing line with no newline yet (e.g. a cell that
        ends with ``print("...", end="")``). Call once after the cell
        this stream belongs to has finished executing — there's nothing
        left after that point that could complete the line another way.
        """
        if self._on_line is not None and self._partial:
            self._on_line(self._partial)
            self._partial = ""

    def getvalue(self) -> str:
        return self._buffer.getvalue()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._buffer, name)


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

    def __init__(
        self,
        app: AppState,
        *,
        namespace: Mapping[str, Any] | None = None,
        history_file: str | Path | None = None,
    ) -> None:
        config = Config()
        config.InteractiveShell.automagic = True
        config.InteractiveShell.autoawait = True
        config.InteractiveShell.autoindent = True
        config.InteractiveShell.display_page = True
        # This does *not* control whether Ctrl+P/Ctrl+N can recall
        # commands from previous sessions — that's `history_file`/
        # `hist_file` below, and the `history` property further down
        # queries the database directly (`get_tail()`) rather than going
        # through whatever this loads. It only controls how much of the
        # history database IPython caches into its own internal
        # `input_hist_raw`/`_ih`/`%history`-magic bookkeeping at startup,
        # none of which this app uses (Textual owns the input widget, not
        # IPython's own readline/prompt_toolkit line editing) — so this
        # stays 0 purely to skip a startup query this app has no use for.
        config.InteractiveShell.history_load_length = 0
        # `history_file` is the caller's choice: `None` (every test in
        # this codebase, and any other direct construction) keeps history
        # in `:memory:` — private to this process, thrown away when it
        # exits, nothing shared with any other InteractiveShell on the
        # machine. A real path (only ever passed by the real entry point,
        # `iyzee.tui.app.run` → `default_history_file()`) makes it
        # persistent and shared across restarts of *this app specifically*
        # — deliberately not IPython's own default location
        # (~/.ipython/profile_default/history.sqlite), which is shared by
        # every InteractiveShell on the machine and is exactly what caused
        # real, reproducible test-suite hangs before `:memory:` became the
        # default: dozens to thousands of instances (each test builds its
        # own) all registering their own `atexit` history-session-end
        # write against one ever-growing shared file, serializing against
        # each other and against however large it's grown by shutdown.
        # `default_history_file()`'s own docstring has the rest of that
        # story. A real *app* run only ever builds one InteractiveShell
        # for its whole lifetime, so the growth-over-time here is orders
        # of magnitude slower than what a test run did to the shared
        # default — normal personal-history-file territory, not that.
        if history_file is None:
            config.HistoryManager.hist_file = ":memory:"
        else:
            history_file = Path(history_file)
            history_file.parent.mkdir(parents=True, exist_ok=True)
            config.HistoryManager.hist_file = str(history_file)
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
        config.Completer.policy_overrides = {"allowed_getattr": {LabProxy, LockedProxy}}
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

    def execute(
        self,
        source: str,
        *,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
    ) -> ExecutionOutput:
        """Execute one cell and capture terminal-oriented output.

        ``on_stdout_line``/``on_stderr_line``, if given, are called once
        per complete line *as the cell produces it* — before this method
        returns, not only once the whole cell has finished — so a caller
        wired up to stream that into a UI (see
        ``IyzeeConsole._execute``) can show progress from a long-running
        cell live instead of only seeing anything once it's done. Callers
        that only want the final text (every direct call in this file's
        tests, for instance) can omit both and get exactly the previous
        behavior: nothing happens until this method returns, then
        ``result.stdout``/``.stderr`` has everything.
        """
        source = source.rstrip()
        if not source.strip():
            return ExecutionOutput(
                source="",
                stdout="",
                stderr="",
                execution_count=self.shell.execution_count,
                success=True,
            )
        stdout = _StreamTee(on_stdout_line)
        stderr = _StreamTee(on_stderr_line)
        with self._lock, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.shell.run_cell(source, store_history=True, silent=False)
        # Whatever's left after the redirect above exits (e.g. a cell
        # that ends with `print("...", end="")`, no trailing newline) —
        # nothing else is going to complete that line, so flush it now.
        stdout.finish_partial_line()
        stderr.finish_partial_line()
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

    #: Cap on how many past entries `history` pulls back via `get_tail()`.
    #: With `hist_file` persistent (see `__init__`) the underlying table
    #: can span months of real use; every `history` access re-queries it
    #: (`history_available`, every Ctrl+P/Ctrl+N — see console.py), so an
    #: unbounded `SELECT` would get slower the longer the app's been in
    #: use. 500 is comfortably more than anyone browses via repeated
    #: Ctrl+P in practice, and `get_tail()` enforces it with a SQL
    #: `LIMIT`, not a Python-side slice — the query itself stays cheap
    #: regardless of table size.
    _HISTORY_TAIL_LIMIT = 500

    @property
    def history(self) -> list[str]:
        """Return input history, oldest first, deduped, most recent last.

        Sourced from ``history_manager.get_tail()`` — the same lookup
        IPython's own terminal frontend uses for its persistent,
        cross-session up/down history (see
        ``IPython.terminal.interactiveshell.PtkHistoryAdapter``) — rather
        than ``get_range(session=0)``, which only the *current* session.
        With a persistent ``hist_file`` (see ``__init__``) that means
        Ctrl+P recalls commands from a previous run of the app, not just
        this one; with the ``:memory:`` default it's equivalent to the
        old session-scoped behavior anyway, since nothing before this
        process existed to recall. Consecutive blank/duplicate entries
        are dropped, the same filtering ``PtkHistoryAdapter`` applies.
        """
        with self._lock:
            entries: list[str] = []
            last = ""
            # traitlets' Instance descriptor makes this Any | None to
            # mypy; IPython's own source (interactiveshell.py) asserts
            # the same thing before use rather than narrowing the
            # declared type, so this follows that convention.
            assert self.shell.history_manager is not None
            tail = self.shell.history_manager.get_tail(
                n=self._HISTORY_TAIL_LIMIT, raw=True, include_latest=True
            )
            for _session, _line, cell in tail:
                cell = cell.rstrip()
                if cell and cell != last:
                    entries.append(cell)
                    last = cell
            return entries

    def close(self) -> None:
        """Run IPython's shutdown hooks."""
        with self._lock:
            self.shell.atexit_operations()
