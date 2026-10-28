"""Tests for LabProxy and the shell configuration (history, namespace).

Uses a small FakeApp standing in for the real IyzeeApp — LabProxy only
needs the two attributes in ipython.AppState (handles, last_run), so
tests don't need a running Textual app to exercise it.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import FakeApp, FakeHandle, history_manager
from IPython.core.completer import provisionalcompleter
from IPython.core.interactiveshell import InteractiveShell

from iyzee.devices.handles import LockedProxy
from iyzee.devices.wavemeter import Wavemeter
from iyzee.experiment import StepResult
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

    def complete(self, source: str, cursor_pos: int) -> list[str]:
        with provisionalcompleter():
            return [
                completion.text
                for completion in self.shell.Completer.completions(source, cursor_pos)
            ]

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


def test_the_lab_namespace_is_live_read_only_and_never_collides_with_user_variables(
    monkeypatch,
) -> None:
    # -- ipython keeps its namespace and a users own variables never collide with lab
    marker = object()
    shell = IyzeeIPython(FakeApp(), namespace={"instrument": marker})

    seeded = shell.execute("instrument")
    assert seeded.success and "object at" in seeded.stdout

    repeated = shell.execute("measurement = 21")
    result = shell.execute("measurement * 2")
    assert repeated.success and result.success and "42" in result.stdout
    assert isinstance(shell.shell.user_ns["lab"], LabProxy)

    shell.execute("mx = 5")  # shadowing lab.mx's old bare name is an ordinary variable
    assert "10" in shell.execute("mx * 2").stdout
    assert isinstance(shell.shell.user_ns["lab"], LabProxy)  # lab is untouched

    # -- lab reflects the current handles live lists every name and is read only
    device = object()
    app = FakeApp(
        handles={"mxa": FakeHandle(device=device), "scope": FakeHandle(device="scope-device")}
    )
    shell = IyzeeIPython(app)
    lab = LabProxy(app)

    assert shell.execute("lab.mx").success
    assert isinstance(lab.mx, LockedProxy) and repr(lab.mx) == repr(device)
    assert repr(lab.scope) == repr("scope-device")
    assert set(lab.connected) == {"mx", "scope"}
    # dir() lists everything possible, connected or not
    assert {"mx", "psu", "shutter", "scope", "results", "last_run", "connected"} <= set(dir(lab))

    # Disconnect: no refresh call needed anywhere, lab.mx just stops resolving because it
    # reads app.handles fresh every time.
    del app.handles["mxa"]
    output = shell.execute("lab.mx")
    assert not output.success and "not connected" in output.stdout

    with pytest.raises(AttributeError):
        lab.mx = "nope"

    # -- the wavemeter is on lab before connecting and behind the lock after
    monkeypatch.setattr(Wavemeter, "read_frequency", lambda self, channel=None: 377.105)

    lab = LabProxy(FakeApp())  # not connected: a plain client is still there
    assert isinstance(lab.wavemeter, Wavemeter)
    assert lab.wavemeter.read_frequency(4) == pytest.approx(377.105)

    matches = IyzeeIPython(FakeApp()).complete("lab.wavemeter.", len("lab.wavemeter."))
    assert ".read_frequency" in matches and ".set_pid_setpoint" in matches  # and tab-completes

    app = FakeApp(handles={"wavemeter": FakeHandle(device=Wavemeter())})  # connected
    assert LabProxy(app).wavemeter.read_frequency(4) == pytest.approx(377.105)

    # -- lab results and last run reflect the latest sweep
    app = FakeApp()
    lab = LabProxy(app)
    assert lab.results == []
    assert lab.last_run is None

    app.last_run = LastRun(kind="bandwidth", results=[StepResult("pt0", 0.0, "Hz", {})], path=None)
    assert lab.results == app.last_run.results
    last_run = lab.last_run
    assert last_run is not None
    assert last_run.kind == "bandwidth"


def test_lab_tab_completion_lists_lab_attributes_and_device_methods() -> None:
    """Jedi (IPython's default completer) does static analysis and can't see
    through LabProxy's dynamic __getattr__; without use_jedi=False this silently
    returns zero completions, which would make ``lab`` undiscoverable in practice."""

    class Device:
        def configure(self) -> None: ...

        def single_sweep_wait(self) -> None: ...

    shell = IyzeeIPython(FakeApp(handles={"mxa": FakeHandle(device=Device())}))

    matches = shell.complete("lab.", len("lab."))
    assert {".mx", ".results", ".last_run", ".connected"} <= set(matches)

    matches = shell.complete("lab.mx.", len("lab.mx."))
    assert {".configure", ".single_sweep_wait"} <= set(matches)

    matches = shell.complete("lab.mx.si", len("lab.mx.si"))
    assert ".single_sweep_wait" in matches


def test_history_skips_repeated_commands_and_is_private_to_each_shell() -> None:
    """Memory history is an isolation boundary: a fresh shell must not inherit another's input
    (IPython's normal history points every shell at one shared on-disk database)."""
    first = IyzeeIPython(FakeApp())
    for src in ["x = 1", "y = 2", "y = 2", "z = x + y"]:
        first.execute(src)
    assert first.history == ["x = 1", "y = 2", "z = x + y"]
    assert history_manager(first.shell).hist_file == ":memory:"

    assert IyzeeIPython(FakeApp()).history == []


def test_history_files_persist_and_default_to_a_per_user_directory(tmp_path: Path) -> None:
    # -- file history persists and creates its parent
    # An explicit history file is opt-in and must survive a shell restart.
    #
    # IPython may buffer recent history entries in memory, so this test flushes
    # the first shell's cache explicitly before constructing the second shell.
    # That proves persistence without calling ``atexit_operations`` twice on one
    # shell, which is not how the real app lifecycle behaves.
    history_file = tmp_path / "not" / "yet" / "created" / "console_history.sqlite"
    assert not history_file.parent.exists()

    first_run = IyzeeIPython(FakeApp(), history_file=history_file)
    first_run.execute("yesterday = 1")
    history_manager(first_run.shell).writeout_cache()

    second_run = IyzeeIPython(FakeApp(), history_file=history_file)
    second_run.execute("today = 2")

    assert history_file.parent.exists()
    assert second_run.history == ["yesterday = 1", "today = 2"]

    # -- default history file is under a per user data directory
    # Deliberately *not* IPython's own default location
    # (`~/.ipython/profile_default/history.sqlite`) -- that one's shared by
    # every InteractiveShell on the machine, this app's included, which is
    # exactly what caused real test-suite hangs before `:memory:` became
    # IyzeeIPython's own default (see the tests above). This app gets its
    # own dedicated file instead.
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
