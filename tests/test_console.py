"""Focused end-to-end coverage for the Textual IPython console."""

from __future__ import annotations

import time

from helpers import async_test
from textual_vim_textarea import Mode, VimTextArea

from iyzee.experiment import StepResult
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import IyzeeConsole
from iyzee.tui.workers import LastRun


async def _run_cell(pilot, console: IyzeeConsole, source: str, key: str = "ctrl+enter") -> None:
    editor = console.query_one(VimTextArea)
    editor.text = source
    editor.cursor_location = editor.document.end
    await pilot.press(key)
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

        await _run_cell(pilot, console, "answer = 40", key="shift+enter")
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
        editor = app.query_one(IyzeeConsole).query_one(VimTextArea)
        for typed, expected in (("pri", "print"), ("lab.m", "lab.mx"), ("x = lab.m", "x = lab.mx")):
            editor.text = typed
            editor.cursor_location = (0, len(typed))
            await pilot.press("ctrl+space")
            await pilot.pause()
            assert editor.text == expected
            assert editor.cursor_location == (0, len(expected))


@async_test
async def test_tab_completes_in_insert_mode_and_indents_without_matches() -> None:
    async with IyzeeApp().run_test(size=(110, 32)) as pilot:
        await pilot.press("i")
        await pilot.pause()
        editor = pilot.app.query_one(VimTextArea)
        await pilot.press("i")
        await pilot.pause()
        assert editor.mode is Mode.INSERT

        await pilot.press("p", "r", "i", "tab")
        assert editor.text == "print"

        # A comment is not an IPython completion target, so Tab keeps its
        # editor indentation behavior instead of listing unrelated names.
        editor.text = "#"
        editor.cursor_location = (0, 1)
        await pilot.press("tab")
        assert editor.text == "#   "

        editor.text = ""
        editor.cursor_location = (0, 0)
        await pilot.press("tab")
        assert editor.text == "    "


@async_test
async def test_vim_delete_and_undo_work_in_console_editor() -> None:
    async with IyzeeApp().run_test(size=(110, 32)) as pilot:
        await pilot.press("i")
        editor = pilot.app.query_one(VimTextArea)
        editor.text = "one two"
        editor.cursor_location = (0, 0)
        await pilot.press("escape", "d", "w")
        assert editor.text == "two"
        await pilot.press("u")
        assert editor.text == "one two"
