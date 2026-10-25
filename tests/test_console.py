"""Focused end-to-end coverage for the Textual IPython console."""

from __future__ import annotations

import time

from helpers import async_test
from textual.widgets import TextArea

from iyzee.experiment import StepResult
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import IyzeeConsole
from iyzee.tui.workers import LastRun


async def _run_cell(pilot, console: IyzeeConsole, source: str) -> None:
    editor = console.query_one(TextArea)
    editor.text = source
    await pilot.press("ctrl+enter")
    deadline = time.monotonic() + 8
    while console.session.executing and time.monotonic() < deadline:
        await pilot.pause(0.03)
    assert not console.session.executing, f"cell did not finish: {source!r}"
    await pilot.pause(0.05)


@async_test
async def test_console_cells_share_state_and_keep_live_lab_results() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("i")
        await pilot.pause()
        console = app.query_one(IyzeeConsole)
        shell = console.session.shell

        await _run_cell(pilot, console, "answer = 40")
        assert shell.user_ns["answer"] == 40

        await _run_cell(pilot, console, "answer + 2")
        assert shell.user_ns["Out"][2] == 42

        lab = shell.user_ns["lab"]
        assert lab.results == [] and lab.last_run is None
        result = StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})
        app.last_run = LastRun(kind="bandwidth", results=[result], path=None)
        assert lab.results == [result]
        assert lab.last_run is not None and lab.last_run.kind == "bandwidth"

    session = console.session
    assert session._thread is not None and not session._thread.is_alive()


@async_test
async def test_completion_replaces_the_word_under_the_cursor() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("i")
        await pilot.pause()
        editor = app.query_one(IyzeeConsole).query_one(TextArea)
        for typed, expected in (("pri", "print"), ("lab.m", "lab.mx"), ("x = lab.m", "x = lab.mx")):
            editor.text = typed
            editor.cursor_location = (0, len(typed))
            await pilot.press("ctrl+space")
            await pilot.pause()
            assert editor.text == expected
            assert editor.cursor_location == (0, len(expected))
