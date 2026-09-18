"""Tests for IyzeeIPython and LabProxy.

Uses a small FakeApp standing in for the real IyzeeApp — LabProxy only
needs the three attributes in ipython.AppState (handles, instrument_locks,
last_run), so tests don't need a running Textual app to exercise it.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from iyzee.experiment import StepResult
from iyzee.tui.instruments import LockedProxy
from iyzee.tui.ipython import IyzeeIPython, LabProxy
from iyzee.tui.workers import LastRun


@dataclass
class FakeApp:
    """Minimal stand-in for IyzeeApp satisfying ipython.AppState."""

    handles: dict[str, Any] = field(default_factory=dict)
    instrument_locks: dict[str, threading.Lock] = field(
        default_factory=lambda: defaultdict(threading.Lock)
    )
    last_run: LastRun | None = None


class MxaHandle:
    def __init__(self, device: Any) -> None:
        self.device = device


class ScopeHandle:
    def __init__(self, scope: Any) -> None:
        self.scope = scope


def test_ipython_executes_against_seeded_namespace() -> None:
    marker = object()
    shell = IyzeeIPython(FakeApp(), namespace={"instrument": marker})
    output = shell.execute("instrument")
    assert output.success
    assert "object at" in output.stdout


def test_ipython_preserves_variables_between_cells() -> None:
    shell = IyzeeIPython(FakeApp())
    first = shell.execute("measurement = 21")
    second = shell.execute("measurement * 2")
    assert first.success
    assert second.success
    assert "42" in second.stdout


def test_lab_is_available_in_the_shell() -> None:
    shell = IyzeeIPython(FakeApp())
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


def test_history_does_not_leak_across_shell_instances() -> None:
    """A fresh IyzeeIPython (e.g. after an app restart) should not surface
    another instance's input. Meaningful regression coverage even though
    each shell's history now lives in its own private `:memory:` database
    (see `test_history_never_touches_a_real_file_on_disk` below) rather
    than a file multiple instances could in principle collide over --
    isolation still isn't automatic, since `InteractiveShell._instance` is
    a shared class-level singleton pointer this app reassigns on every
    construction (see the comment where that happens in `ipython.py`)."""
    first = IyzeeIPython(FakeApp())
    first.execute("secret = 1")
    assert first.history == ["secret = 1"]

    second = IyzeeIPython(FakeApp())
    assert second.history == []


def test_history_never_touches_a_real_file_on_disk() -> None:
    """Every InteractiveShell writes its input history to SQLite by
    default, and unless told otherwise that means a real, persistent file
    shared across every instance ever constructed on the machine
    (`~/.ipython/profile_default/history.sqlite`) -- one this app never
    even reads back (`history_load_length = 0`), so it was pure overhead,
    and it never gets cleaned up: a full test run alone constructs 100+
    shells, each leaving a row behind forever. `hist_file = ":memory:"`
    keeps each shell's history entirely private and disposable instead --
    this pins that configuration down directly (rather than only
    exercising the behavior it happens to produce) so a future change
    can't silently drop it and have every symptom show up only much
    later, as slowdown, rather than as a clear test failure."""
    shell = IyzeeIPython(FakeApp())
    assert shell.shell.history_manager.hist_file == ":memory:"

    # And the thing history is actually used for here -- current-session
    # recall for Ctrl+P/Ctrl+N -- behaves identically to a real file.
    shell.execute("1 + 1")
    assert shell.history == ["1 + 1"]


def test_locked_proxy_serializes_concurrent_calls() -> None:
    import time

    class SlowDevice:
        def __init__(self) -> None:
            self.max_concurrent = 0
            self._active = 0
            self._guard = threading.Lock()

        def do_thing(self) -> None:
            with self._guard:
                self._active += 1
                self.max_concurrent = max(self.max_concurrent, self._active)
            time.sleep(0.05)
            with self._guard:
                self._active -= 1

    device = SlowDevice()
    proxy = LockedProxy(device, threading.Lock())

    threads = [threading.Thread(target=proxy.do_thing) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert device.max_concurrent == 1


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


def test_execute_streams_stdout_one_complete_line_at_a_time() -> None:
    """`on_stdout_line` is the mechanism the console uses to show output
    live instead of only once a cell finishes (plan item #4) — this pins
    down its actual contract: called once per *complete* line, in order,
    as the cell produces them, before `execute()` returns."""
    shell = IyzeeIPython(FakeApp())
    lines: list[str] = []

    result = shell.execute(
        "print('one')\nprint('two')\nprint('three')",
        on_stdout_line=lines.append,
    )

    assert lines == ["one", "two", "three"]
    # And the callback isn't a replacement for the final captured text --
    # every other caller (every test above, for instance) still gets the
    # complete `stdout` back exactly as before.
    assert result.stdout == "one\ntwo\nthree\n"


def test_execute_does_not_fragment_a_single_print_call_across_lines() -> None:
    """`print("a", "b")` makes several separate `file.write()` calls under
    the hood (one per argument, one for the separator, one for the
    trailing newline) -- naively forwarding each `write()` as its own
    line would turn one logical line of output into several. The
    callback must only fire once the *line* is complete, not once each
    underlying `write()` call happens."""
    shell = IyzeeIPython(FakeApp())
    lines: list[str] = []

    shell.execute("print('a', 'b', 'c')", on_stdout_line=lines.append)

    assert lines == ["a b c"]


def test_execute_flushes_a_trailing_line_with_no_newline() -> None:
    """`print("...", end="")` never produces a newline of its own -- the
    callback must still see that partial final line once the cell is
    done, not lose it entirely."""
    shell = IyzeeIPython(FakeApp())
    lines: list[str] = []

    result = shell.execute("print('no newline', end='')", on_stdout_line=lines.append)

    assert lines == ["no newline"]
    assert result.stdout == "no newline"


def test_execute_streams_stdout_and_stderr_independently() -> None:
    shell = IyzeeIPython(FakeApp())
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    shell.execute(
        "import sys\nprint('to stdout')\nprint('to stderr', file=sys.stderr)",
        on_stdout_line=stdout_lines.append,
        on_stderr_line=stderr_lines.append,
    )

    assert stdout_lines == ["to stdout"]
    assert stderr_lines == ["to stderr"]


def test_execute_preserves_intentional_blank_lines_in_streamed_output() -> None:
    """A bare `print()` is a deliberate blank line, not trailing
    whitespace to trim -- the streaming callback should pass it through
    like any other line rather than collapsing or dropping it."""
    shell = IyzeeIPython(FakeApp())
    lines: list[str] = []

    shell.execute("print('one')\nprint()\nprint('two')", on_stdout_line=lines.append)

    assert lines == ["one", "", "two"]
