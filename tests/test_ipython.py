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

    app.last_run = LastRun(kind="bandwidth", results=["pt0"], path=None)
    assert lab.results == ["pt0"]
    assert lab.last_run.kind == "bandwidth"


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
    another instance's input, even though IPython persists history to a
    shared sqlite file on disk by default."""
    first = IyzeeIPython(FakeApp())
    first.execute("secret = 1")
    assert first.history == ["secret = 1"]

    second = IyzeeIPython(FakeApp())
    assert second.history == []


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
