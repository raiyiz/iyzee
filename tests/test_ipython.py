"""Tests for LabProxy and the shell configuration (history, namespace).

Uses a small FakeApp standing in for the real IyzeeApp — LabProxy only
needs the three attributes in ipython.AppState (handles, instrument_locks,
last_run), so tests don't need a running Textual app to exercise it.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helpers import FakeApp, history_manager
from IPython.core.interactiveshell import InteractiveShell

from iyzee.experiment import StepResult
from iyzee.tui.instruments import LockedProxy
from iyzee.tui.ipython import LabProxy, default_history_file, lab_namespace, shell_config
from iyzee.tui.workers import LastRun


@dataclass
class _Result:
    stdout: str
    stderr: str
    success: bool


class IyzeeIPython:
    """Minimal test harness: a plain InteractiveShell built from the same
    ``shell_config``/``lab_namespace`` the console's real (terminal) shell
    uses, with a ``run``-and-capture ``execute``. The terminal shell itself
    is covered in test_ipython_session.py."""

    def __init__(self, app, namespace=None, history_file=None) -> None:
        self.shell = InteractiveShell(
            config=shell_config(history_file), user_ns=lab_namespace(app, namespace)
        )
        InteractiveShell._instance = self.shell  # type: ignore[assignment]

    def execute(self, source: str) -> _Result:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = self.shell.run_cell(source, store_history=True, silent=False)
        return _Result(out.getvalue(), err.getvalue(), result.error_in_exec is None)

    def complete(self, source: str, cursor_pos: int):
        return self.shell.complete(source, line=source, cursor_pos=cursor_pos)

    @property
    def history(self) -> list[str]:
        tail = history_manager(self.shell).get_tail(n=500, raw=True, include_latest=True)
        entries: list[str] = []
        for _s, _l, cell in tail:
            cell = cell.rstrip()
            if cell and (not entries or entries[-1] != cell):
                entries.append(cell)
        return entries

    def close(self) -> None:
        self.shell.atexit_operations()


class MxaHandle:
    def __init__(self, device: Any) -> None:
        self.device = device


class ScopeHandle:
    def __init__(self, scope: Any) -> None:
        self.scope = scope


def test_ipython_executes_and_preserves_the_lab_namespace() -> None:
    marker = object()
    shell = IyzeeIPython(FakeApp(), namespace={"instrument": marker})

    seeded = shell.execute("instrument")
    assert seeded.success and "object at" in seeded.stdout

    repeated = shell.execute("measurement = 21")
    result = shell.execute("measurement * 2")
    assert repeated.success and result.success and "42" in result.stdout
    assert isinstance(shell.shell.user_ns["lab"], LabProxy)


def test_lab_attribute_reflects_current_handles_live() -> None:
    device = object()
    app = FakeApp(handles={"mxa": MxaHandle(device)})
    shell = IyzeeIPython(app)

    output = shell.execute("lab.mx")
    assert output.success

    # Disconnect: no refresh call needed anywhere, lab.mx just stops
    # resolving because it reads app.handles fresh every time.
    del app.handles["mxa"]
    output = shell.execute("lab.mx")
    assert not output.success
    assert "not connected" in output.stdout


def test_lab_mx_is_wrapped_in_locked_proxy() -> None:
    device = object()
    app = FakeApp(handles={"mxa": MxaHandle(device)})
    lab = LabProxy(app)
    assert isinstance(lab.mx, LockedProxy)
    assert repr(lab.mx) == repr(device)


def test_lab_scope_unwraps_the_scope_attribute() -> None:
    app = FakeApp(handles={"scope": ScopeHandle("scope-device")})
    lab = LabProxy(app)
    assert repr(lab.scope) == repr("scope-device")


def test_lab_results_and_last_run_reflect_the_latest_sweep() -> None:
    app = FakeApp()
    lab = LabProxy(app)
    assert lab.results == []
    assert lab.last_run is None

    app.last_run = LastRun(kind="bandwidth", results=[StepResult("pt0", 0.0, "Hz", {})], path=None)
    assert lab.results == app.last_run.results
    last_run = lab.last_run
    assert last_run is not None
    assert last_run.kind == "bandwidth"


def test_lab_is_read_only() -> None:
    lab = LabProxy(FakeApp())
    try:
        lab.mx = "nope"
    except AttributeError:
        pass
    else:
        raise AssertionError("expected AttributeError")


def test_lab_connected_and_dir_reflect_current_state() -> None:
    app = FakeApp(handles={"mxa": MxaHandle(object())})
    lab = LabProxy(app)
    assert lab.connected == ("mx",)
    # dir() always lists the full possible set, connected or not
    assert {"mx", "shutter", "scope", "results", "last_run"} <= set(dir(lab))


def test_a_users_own_mx_variable_never_collides_with_lab() -> None:
    """The whole point of `lab.mx` over a bare `mx`: shadowing it is a
    completely ordinary variable, with zero interaction with app state."""
    shell = IyzeeIPython(FakeApp())
    shell.execute("mx = 5")
    result = shell.execute("mx * 2")
    assert "10" in result.stdout
    # lab is untouched and still resolves normally
    assert isinstance(shell.shell.user_ns["lab"], LabProxy)


def test_lab_device_tab_completion_works() -> None:
    class Device:
        def configure(self) -> None: ...

        def single_sweep_wait(self) -> None: ...

    app = FakeApp(handles={"mxa": MxaHandle(Device())})
    shell = IyzeeIPython(app)

    _prefix, matches = shell.complete("lab.mx.", len("lab.mx."))
    assert {".configure", ".single_sweep_wait"} <= set(matches)

    _prefix, matches = shell.complete("lab.mx.si", len("lab.mx.si"))
    assert ".single_sweep_wait" in matches


def test_lab_tab_completion_actually_works() -> None:
    """Jedi (IPython's default completer) does static analysis and can't
    see through LabProxy's dynamic __getattr__ -- without use_jedi=False
    this silently returns zero completions for `lab.<Tab>`, which would
    make `lab`'s whole attribute set undiscoverable in practice."""
    app = FakeApp(handles={"mxa": MxaHandle(object())})
    shell = IyzeeIPython(app)
    _text, matches = shell.complete("lab.", 4)
    assert {".mx", ".results", ".last_run", ".connected"} <= set(matches)

    shell = IyzeeIPython(FakeApp())
    for src in ["x = 1", "y = 2", "y = 2", "z = x + y"]:
        shell.execute(src)

    assert shell.history == ["x = 1", "y = 2", "z = x + y"]


def test_memory_history_is_private_to_each_shell_instance() -> None:
    """A fresh shell must not inherit input history from another instance.

    The app deliberately uses a private ``:memory:`` SQLite database by default.
    That is an isolation boundary, not merely an optimization: IPython's normal
    persistent history points every shell at a shared on-disk database, while this
    application owns the lifecycle of its shell and should not leak commands across
    app instances.
    """
    first = IyzeeIPython(FakeApp())
    first.execute("secret = 1")
    assert first.history == ["secret = 1"]
    assert history_manager(first.shell).hist_file == ":memory:"

    second = IyzeeIPython(FakeApp())
    assert second.history == []


def test_file_history_persists_and_creates_its_parent(tmp_path: Path) -> None:
    """An explicit history file is opt-in and must survive a shell restart.

    IPython may buffer recent history entries in memory, so this test flushes
    the first shell's cache explicitly before constructing the second shell.
    That proves persistence without calling ``atexit_operations`` twice on one
    shell, which is not how the real app lifecycle behaves.
    """
    history_file = tmp_path / "not" / "yet" / "created" / "console_history.sqlite"
    assert not history_file.parent.exists()

    first_run = IyzeeIPython(FakeApp(), history_file=history_file)
    first_run.execute("yesterday = 1")
    history_manager(first_run.shell).writeout_cache()

    second_run = IyzeeIPython(FakeApp(), history_file=history_file)
    second_run.execute("today = 2")

    assert history_file.parent.exists()
    assert second_run.history == ["yesterday = 1", "today = 2"]


def test_default_history_file_is_under_a_per_user_data_directory() -> None:
    """Deliberately *not* IPython's own default location
    (`~/.ipython/profile_default/history.sqlite`) -- that one's shared by
    every InteractiveShell on the machine, this app's included, which is
    exactly what caused real test-suite hangs before `:memory:` became
    IyzeeIPython's own default (see the tests above). This app gets its
    own dedicated file instead."""
    path = default_history_file()

    assert path.name == "console_history.sqlite"
    assert ".ipython" not in path.parts


def test_help_syntax_does_not_hit_the_interactive_pager() -> None:
    """Regression test: `name?`/`name??` route through IPython's own pager
    machinery (`page.page()`), which consults the module-level
    `get_ipython()` singleton to find hooks — including the
    `display_page=True` hook this shell configures specifically so pager
    output becomes plain output instead of an interactive `less`-style
    prompt. If the shell never registers itself as that singleton,
    `get_ipython()` returns None, `page()` skips the hook entirely, and
    falls back to a real interactive pager that blocks on stdin and raises
    EOFError the moment it can't read a keypress — exactly what happens
    when Textual owns the terminal instead.
    """
    shell = IyzeeIPython(FakeApp())

    result = shell.execute("lab?")

    assert result.success
    assert "Docstring" in result.stdout
    assert "EOFError" not in result.stdout
