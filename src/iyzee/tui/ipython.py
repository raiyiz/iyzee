"""What the console's IPython shell is made of: the ``lab`` namespace,
its locking, and its configuration. (Running the shell lives in
``ipython_session.py``.)"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol

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


def shell_config(history_file: str | Path | None = None) -> Config:
    """IPython configuration shared by the console's shell.

    ``history_file`` is the caller's choice: ``None`` (every test in this
    codebase, and any other direct construction) keeps history in
    ``:memory:`` — private to this process, thrown away when it exits,
    nothing shared with any other ``InteractiveShell`` on the machine. A
    real path (only ever passed by the real entry point,
    ``iyzee.tui.app.run`` -> ``default_history_file()``) makes it persistent
    and shared across restarts of *this app specifically* — deliberately not
    IPython's own default location (``~/.ipython/profile_default/
    history.sqlite``), which is shared by every ``InteractiveShell`` on the
    machine and is what caused real, reproducible test-suite hangs before
    ``:memory:`` became the default: dozens to thousands of instances (each
    test builds its own) all registering their own ``atexit``
    history-session-end write against one ever-growing shared file,
    serializing against each other and against however large it had grown
    by shutdown. ``default_history_file()``'s own docstring has the rest of
    that story. A real *app* run only ever builds one shell for its whole
    lifetime, so growth over time here is orders of magnitude slower than
    what a test run did to the shared default.
    """
    config = Config()
    config.InteractiveShell.automagic = True
    config.InteractiveShell.autoawait = True
    config.InteractiveShell.autoindent = True
    config.InteractiveShell.display_page = True
    # How many entries IPython's prompt loads for Up/Down and Ctrl+R recall
    # (its prompt_toolkit history adapter reads exactly this many from the
    # history database). Must NOT be 0: that leaves the prompt with an empty
    # history, so Up/Down and Ctrl+R find nothing — in the same session as
    # well as across restarts.
    config.InteractiveShell.history_load_length = 1000
    if history_file is None:
        config.HistoryManager.hist_file = ":memory:"
    else:
        history_file = Path(history_file)
        history_file.parent.mkdir(parents=True, exist_ok=True)
        config.HistoryManager.hist_file = str(history_file)
    config.InteractiveShell.log_output = False
    config.InteractiveShell.banner1 = ""
    config.InteractiveShell.banner2 = ""
    # Jedi does static analysis and can't see through LabProxy's dynamic
    # __getattr__ — with it on, `lab.<Tab>` silently returns no completions
    # at all (checked directly: Jedi returns nothing for `lab.` and
    # IPython's complete() does not fall back to its own simpler dir()-based
    # completer when that happens). Regular attribute/module/keyword
    # completion elsewhere is unaffected.
    config.Completer.use_jedi = False
    # Let IPython's completer look through the proxies: without this,
    # nested completion such as `lab.mx.<Tab>` finds nothing, because the
    # completer will not call getattr on objects it does not trust.
    config.Completer.policy_overrides = {"allowed_getattr": {LabProxy, LockedProxy}}
    return config


def lab_namespace(app: AppState, namespace: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The shell's user namespace: ``namespace`` plus a single ``lab``.

    Set once, never reassigned — see LabProxy's docstring for why this
    replaces refreshing a set of copied globals on every visit.
    """
    user_ns = dict(namespace or {})
    user_ns["lab"] = LabProxy(app)
    return user_ns
