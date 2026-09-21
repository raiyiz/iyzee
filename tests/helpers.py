"""Shared scaffolding for the TUI tests.

Everything here used to be re-implemented, in two or three slightly different
forms, in each test file: the ``asyncio.run(scenario())`` wrapper, the fake
instrument handle, "connect the fake on the Connect page", "wait for
something to happen", "read a widget's text". One version of each lives here.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ParamSpec, cast

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Static

from iyzee.experiment import StepResult, save_step_results
from iyzee.tui import app as app_mod
from iyzee.tui.instruments import InstrumentSpec
from iyzee.tui.screens import connect as connect_mod
from iyzee.tui.screens.connect import ConnectScreen
from iyzee.tui.workers import LastRun

P = ParamSpec("P")


def async_test(fn: Callable[P, Awaitable[None]]) -> Callable[P, None]:
    """Run an ``async def`` test to completion.

    Replaces the ``async def scenario(): ...`` / ``asyncio.run(scenario())``
    pair every TUI test used to carry. ``functools.wraps`` keeps the original
    signature visible to pytest, so fixtures and ``parametrize`` work as usual.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(fn(*args, **kwargs))

    return wrapper


async def wait_until(
    pilot: Pilot[Any], predicate: Callable[[], object], *, timeout: float = 6.0
) -> None:
    """Let the app run until ``predicate()`` is truthy (or fail after ``timeout``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not reached within timeout")


class FakeHandle:
    """A controllable ``InstrumentHandle``.

    Also carries ``device`` / ``shutter`` so the Sweep page treats it as a
    connected MXA / shutter, and counts what was done to it.
    """

    def __init__(
        self,
        *,
        probe: str = "fake ready",
        connect_error: str | None = None,
        probe_error: str | None = None,
        disconnect_error: str | None = None,
        connect_delay: float = 0.0,
        device: object | None = None,
    ) -> None:
        self._probe = probe
        self.connect_error = connect_error
        self.probe_error = probe_error
        self.disconnect_error = disconnect_error
        self.connect_delay = connect_delay
        self.device = device if device is not None else object()
        self.shutter = object()
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def connected(self) -> bool:
        return self.connect_calls > 0

    @property
    def disconnected(self) -> bool:
        return self.disconnect_calls > 0

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_delay:
            time.sleep(self.connect_delay)
        if self.connect_error:
            raise ConnectionError(self.connect_error)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_error:
            raise ConnectionError(self.disconnect_error)

    def probe(self) -> str:
        if self.probe_error:
            raise ConnectionError(self.probe_error)
        return self._probe


@dataclass
class FakeApp:
    """Minimal stand-in for ``IyzeeApp`` satisfying ``ipython.AppState``."""

    handles: dict[str, Any] = field(default_factory=dict)
    instrument_locks: dict[str, threading.Lock] = field(
        default_factory=lambda: defaultdict(threading.Lock)
    )
    last_run: LastRun | None = None


def connect_app(
    monkeypatch: pytest.MonkeyPatch,
    *handles: FakeHandle,
    key: str = "fake",
    label: str = "Fake",
) -> app_mod.IyzeeApp:
    """An app whose Connect page lists one instrument, built from ``handles``
    in order (one handle per connect attempt)."""
    queue = list(handles) or [FakeHandle()]
    spec = InstrumentSpec(key=key, label=label, make=lambda: queue.pop(0), short="Fk")
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [spec])
    monkeypatch.setattr(app_mod, "INSTRUMENTS", [spec])
    return app_mod.IyzeeApp()


async def enter_on_first_row(
    app: app_mod.IyzeeApp, pilot: Pilot[Any]
) -> tuple[ConnectScreen, DataTable]:
    """Open the Connect page, put the cursor on the first row, press Enter."""
    await pilot.press("c")
    await pilot.pause()
    screen = app.query_one(ConnectScreen)
    table = screen.query_one(DataTable)
    table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
    await pilot.press("enter")
    return screen, table


def history_manager(shell: Any) -> Any:
    """``shell.history_manager`` (typed Optional by IPython) with the None case ruled out."""
    manager = shell.history_manager
    assert manager is not None
    return manager


def plain(widget: Static) -> str:
    """A ``Static``'s text, without markup."""
    return cast(Any, widget.render()).plain  # type: ignore[no-any-return]


def notifications(app: app_mod.IyzeeApp) -> list[str]:
    return [n.message for n in app._notifications]


def make_result(label: str = "pt0", x: float = 1.0, **meta: Any) -> StepResult:
    return StepResult(
        label=label,
        x_value=x,
        x_unit="Hz",
        traces={"squeezing": [1.0, 2.0], "shot_noise": [1.5, 1.5]},
        meta=dict(meta),
    )


def save_run(
    root: Path, folder: str, *results: StepResult, mtime: float | None = None, **run_metadata: Any
) -> Path:
    """Write one run archive under ``root/folder`` and return its path.

    ``mtime`` pins the file's modification time (the Traces page orders runs
    by it, and filesystem timestamps are too coarse to rely on).
    """
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = save_step_results(
        list(results) or [make_result()], directory, run_metadata=run_metadata or None
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path
