"""Regression tests for the TUI robustness fixes.

Each test is a scenario from the usability audit that used to lose data,
leak an instrument connection, or crash the app:

* error text containing ``[...]`` was parsed as markup (a ``[/...]`` sequence
  raised ``MarkupError`` inside a render and took the whole app down);
* Enter on a row that was still "connecting..." opened the device twice;
* a connect that failed after opening the link left it open;
* quitting never disconnected anything;
* a sweep only wrote its file when it finished, so a crash lost the run.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rich.text import Text
from textual.widgets import DataTable, RichLog, Static

from iyzee.experiment import ExperimentContext, StepResult, save_data, save_step_results
from iyzee.tui import app as app_mod
from iyzee.tui.instruments import InstrumentSpec
from iyzee.tui.screens import connect as connect_mod
from iyzee.tui.screens import sweep as sweep_mod
from iyzee.tui.screens import traces as traces_mod
from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL, ConnectScreen
from iyzee.tui.screens.sweep import SweepScreen
from iyzee.tui.screens.traces import TracesScreen

# -- helpers ---------------------------------------------------------------------------


async def _wait_until(pilot, predicate, *, timeout: float = 5.0) -> None:
    """Poll `predicate` while letting the app process messages."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not reached within timeout")


class _Handle:
    """Instrument stand-in that records what was done to it."""

    def __init__(
        self,
        *,
        connect_error: str | None = None,
        probe_error: str | None = None,
        connect_delay: float = 0.0,
    ) -> None:
        self.connect_error = connect_error
        self.probe_error = probe_error
        self.connect_delay = connect_delay
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1
        time.sleep(self.connect_delay)
        if self.connect_error:
            raise ConnectionError(self.connect_error)

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def probe(self) -> str:
        if self.probe_error:
            raise ConnectionError(self.probe_error)
        return "ready"


def _connect_app(monkeypatch: pytest.MonkeyPatch, handles: list[_Handle]):
    """An app whose Connect page lists one instrument, built from `handles`
    in order (one new handle per connect attempt)."""
    queue = list(handles)
    spec = InstrumentSpec(key="fake", label="Fake", make=lambda: queue.pop(0))
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [spec])
    return app_mod.IyzeeApp()


async def _press_enter_on_first_row(app, pilot) -> ConnectScreen:
    await pilot.press("c")
    await pilot.pause()
    screen = app.query_one(ConnectScreen)
    table = screen.query_one(DataTable)
    table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
    await pilot.press("enter")
    return screen


# -- 1. external text must never be parsed as markup -----------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "could not open [/dev/ttyUSB0]",  # [/...] is a *closing tag*: MarkupError
        "timeout [nan, nan] on read",  # lowercase [...] is a tag: silently swallowed
        "[Errno 113] No route to host",
    ],
)
def test_connect_error_text_is_shown_verbatim_and_cannot_crash_the_app(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    app = _connect_app(monkeypatch, [_Handle(connect_error=message)])

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen = await _press_enter_on_first_row(app, pilot)
            table = screen.query_one(DataTable)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "error")
            # Force a real render of the table (this is where MarkupError blew up).
            await pilot.pause(0.1)
            app.export_screenshot()
            # DataTable parses str cells as markup; what it would display is
            # exactly the original message, brackets and all.
            shown = Text.from_markup(table.get_cell("fake", DETAIL_COL)).plain
            assert shown == message
            # And the toast carries the raw text, unparsed.
            assert any(n.message == f"Fake: {message}" for n in app._notifications)
            assert app.is_running

    asyncio.run(scenario())


def test_sweep_log_keeps_bracketed_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Step:
        label = "pt[0]"

        def run(self, ctx: ExperimentContext) -> StepResult:
            raise NotImplementedError

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            screen = app.query_one(SweepScreen)
            log = screen.query_one("#sweep-log", RichLog)
            for error in ("bad [/oops] tag", "timeout [nan, nan] here"):
                log.clear()
                screen._on_step(0, 1, _Step(), None, RuntimeError(error))  # must not raise
                await pilot.pause()
                rendered = "\n".join("".join(seg.text for seg in line) for line in log.lines)
                assert error in rendered, rendered
                assert "pt[0]" in rendered

    asyncio.run(scenario())


def test_traces_summary_survives_bracketed_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_dir = tmp_path / "2026-09-18_bandwidth"
    run_dir.mkdir()
    results = [
        StepResult(
            label="pt0",
            x_value=1.0,
            x_unit="Hz",
            traces={"squeezing": [1.0, 2.0], "shot_noise": [1.0, 1.0]},
            meta={},
        )
    ]
    save_step_results(results, run_dir, run_metadata={"note": "see [/docs] and [nan, nan]"})
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", tmp_path)

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause()
            screen = app.query_one(TracesScreen)
            screen.query_one("#traces-list").focus()
            await pilot.press("enter")
            await pilot.pause()
            rendered = screen.query_one("#traces-summary", Static).render()
            assert isinstance(rendered, Text)
            assert "see [/docs] and [nan, nan]" in rendered.plain

    asyncio.run(scenario())


# -- 2. no double connect ----------------------------------------------------------------


def test_enter_on_a_connecting_row_does_not_open_the_device_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _Handle(connect_delay=0.4), _Handle(connect_delay=0.4)
    app = _connect_app(monkeypatch, [first, second])

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen = await _press_enter_on_first_row(app, pilot)
            table = screen.query_one(DataTable)
            await pilot.pause(0.1)  # first connect is now in flight
            await pilot.press("enter")  # impatient second press
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            assert first.connect_calls == 1
            assert second.connect_calls == 0, "second Enter must be ignored while busy"
            assert app.handles["fake"] is first

    asyncio.run(scenario())


def test_row_can_be_used_again_once_the_operation_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _Handle()
    app = _connect_app(monkeypatch, [handle])

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen = await _press_enter_on_first_row(app, pilot)
            table = screen.query_one(DataTable)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "connected")
            await _wait_until(pilot, lambda: not screen._busy)
            await pilot.press("enter")  # now this is a disconnect: the first Enter arms it...
            await pilot.press("enter")  # ...and the second confirms
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "disconnected")
            assert handle.disconnect_calls == 1
            assert "fake" not in app.handles

    asyncio.run(scenario())


def test_a_failed_probe_closes_the_half_open_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() succeeded but probe() failed: nothing will ever hold a
    reference to that handle, so the connect path has to close it."""
    handle = _Handle(probe_error="no answer")
    app = _connect_app(monkeypatch, [handle])

    async def scenario() -> None:
        async with app.run_test() as pilot:
            screen = await _press_enter_on_first_row(app, pilot)
            table = screen.query_one(DataTable)
            await _wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == "error")
            assert handle.connect_calls == 1
            assert handle.disconnect_calls == 1
            assert "fake" not in app.handles
            assert table.get_cell("fake", DETAIL_COL) == "no answer"

    asyncio.run(scenario())


# -- 3. quitting disconnects the instruments -----------------------------------------------


def test_exiting_the_app_disconnects_every_connected_instrument() -> None:
    a, b = _Handle(), _Handle()

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.handles["one"], app.handles["two"] = a, b
            await pilot.press("ctrl+q")
            await pilot.pause(0.5)
        assert (a.disconnect_calls, b.disconnect_calls) == (1, 1)
        assert app.handles == {}

    asyncio.run(scenario())


def test_close_instruments_does_not_hang_on_a_busy_instrument() -> None:
    """An instrument whose lock is held (a step that never returns) must not
    be able to block quitting: it is skipped once the deadline passes, and
    the other instruments are still closed."""
    app = app_mod.IyzeeApp()
    busy, idle = _Handle(), _Handle()
    app.handles["busy"], app.handles["idle"] = busy, idle
    lock = app.instrument_locks["busy"]
    lock.acquire()
    try:
        started = time.monotonic()
        app.close_instruments(timeout=0.3)
        elapsed = time.monotonic() - started
    finally:
        lock.release()
    assert elapsed < 2.0
    assert idle.disconnect_calls == 1
    assert busy.disconnect_calls == 0


def test_close_instruments_waits_for_the_lock_then_disconnects() -> None:
    app = app_mod.IyzeeApp()
    handle = _Handle()
    app.handles["mxa"] = handle
    lock = app.instrument_locks["mxa"]
    lock.acquire()
    threading.Timer(0.2, lock.release).start()  # a sweep step finishing
    app.close_instruments(timeout=3.0)
    assert handle.disconnect_calls == 1


def test_ctrl_q_is_advertised_in_the_footer() -> None:
    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            binding = app.screen.active_bindings["ctrl+q"].binding
            assert binding.action == "quit"
            assert binding.show is True

    asyncio.run(scenario())


# -- 4. sweeps are saved point by point --------------------------------------------------


def test_save_data_can_overwrite_a_fixed_file_atomically(tmp_path: Path) -> None:
    # An explicit, non-timestamp name: unlike a timestamped default it can
    # only be produced by honouring `path`.
    target = tmp_path / "checkpoint.npz"
    assert save_data([(1.0, [1.0], [1.0])], tmp_path, path=target) == target
    again = save_data([(1.0, [1.0], [1.0]), (2.0, [2.0], [2.0])], tmp_path, path=target)
    assert again == target
    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkpoint.npz"], "no stray .part file"
    with np.load(target, allow_pickle=True) as archive:
        assert len(archive["data"]) == 2


def _result(index: int) -> StepResult:
    return StepResult(
        label=f"pt{index}",
        x_value=float(index),
        x_unit="Hz",
        traces={"squeezing": [1.0, 2.0], "shot_noise": [1.0, 1.0]},
        meta={},
    )


def _sweep_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_run_sequence):
    """An app wired so pressing Run drives `fake_run_sequence` instead of hardware."""
    monkeypatch.setattr(sweep_mod, "create_dirs", lambda name="": tmp_path)
    monkeypatch.setattr(sweep_mod, "prepare_analyzer", lambda *a, **k: None)
    monkeypatch.setattr(sweep_mod, "run_sequence", fake_run_sequence)
    app = app_mod.IyzeeApp()

    class _Mxa:
        device = object()

        def connect(self) -> None:
            raise NotImplementedError

        def disconnect(self) -> None:
            raise NotImplementedError

        def probe(self) -> str:
            raise NotImplementedError

    app.handles["mxa"] = _Mxa()
    return app


def _points_on_disk(directory: Path) -> int:
    files = list(directory.glob("*.npz"))
    assert len(files) <= 1, f"expected a single checkpoint file, found {files}"
    if not files:
        return 0
    with np.load(files[0], allow_pickle=True) as archive:
        return len(archive["data"])


def test_points_are_on_disk_while_the_sweep_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    on_disk_after_each_step: list[int] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        for index in range(3):
            on_step(index, 3, object(), _result(index), None)
            on_disk_after_each_step.append(_points_on_disk(tmp_path))

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.click("#run-sweep")
            await _wait_until(pilot, lambda: app.last_run is not None)

    asyncio.run(scenario())
    # Before the fix nothing was written until the run ended: [0, 0, 0].
    assert on_disk_after_each_step == [1, 2, 3]
    assert _points_on_disk(tmp_path) == 3
    assert app.last_run is not None
    assert app.last_run.path is not None and app.last_run.path.parent == tmp_path
    assert len(app.last_run.results) == 3


def test_points_are_already_on_disk_at_the_moment_a_run_blows_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The important instant is *before* the run's own cleanup: if the whole
    process died here (power cut, SIGKILL) nothing after this point would
    ever run, so the data has to be on disk already."""
    at_the_moment_of_failure: list[int] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        on_step(0, 5, object(), _result(0), None)
        on_step(1, 5, object(), _result(1), None)
        at_the_moment_of_failure.append(_points_on_disk(tmp_path))
        raise RuntimeError("instrument fell over")

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.click("#run-sweep")
            await _wait_until(pilot, lambda: app.last_run is not None)

    asyncio.run(scenario())
    assert at_the_moment_of_failure == [2]
    assert _points_on_disk(tmp_path) == 2


def test_a_sweep_stops_at_the_next_step_when_the_app_is_shutting_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    holder: dict[str, Any] = {}

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        on_step(0, 5, object(), _result(0), None)
        holder["app"].shutdown_requested.set()  # the user pressed Ctrl+Q
        on_step(1, 5, object(), _result(1), None)  # raises SweepAborted here
        holder["ran_past_shutdown"] = True

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    holder["app"] = app

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.click("#run-sweep")
            await _wait_until(pilot, lambda: app.last_run is not None)

    asyncio.run(scenario())
    assert "ran_past_shutdown" not in holder
    assert _points_on_disk(tmp_path) == 2, "both points, including the one in flight, are saved"


def test_a_failing_save_is_reported_once_and_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps_run: list[int] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        for index in range(3):
            on_step(index, 3, object(), _result(index), None)
            steps_run.append(index)

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)

    def broken_save(*args, **kwargs):
        raise OSError("No space left on device [/data]")

    monkeypatch.setattr(sweep_mod, "save_step_results", broken_save)

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            await pilot.click("#run-sweep")
            await _wait_until(pilot, lambda: app.last_run is not None)
            await pilot.pause(0.2)
            messages = [n.message for n in app._notifications if "Could not save" in n.message]
            assert len(messages) == 1, messages  # once, not once per point
            assert app.is_running

    asyncio.run(scenario())
    assert steps_run == [0, 1, 2], "measurement continued despite the save failure"
    assert app.last_run is not None and len(app.last_run.results) == 3
    assert app.last_run.path is None
