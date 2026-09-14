"""Tests for _ConsoleInput's integration with textual_vim_textarea.VimTextArea:
typing, vim motions, and specifically the two-stage Escape design (1st
Escape -> vim NORMAL and stay focused, 2nd Escape -> blur out of the
console entirely). This is exactly the ordering-sensitive behavior that
was verified manually against the library before relying on it — these
tests pin that down so a future textual-vim-textarea upgrade can't
silently change it.
"""

from __future__ import annotations

import asyncio

from textual_vim_textarea import Mode

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import IyzeeConsole, _ConsoleInput


def test_console_input_starts_in_insert_mode_for_immediate_typing() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            console = app.screen.query_one(IyzeeConsole)
            text_area = app.screen.query_one(_ConsoleInput)
            assert text_area.mode is Mode.INSERT
            for key in "hello":
                await pilot.press(key)
            await pilot.pause()
            assert text_area.text == "hello"
            assert console is not None  # sanity: the screen actually composed

    asyncio.run(scenario())


def test_first_escape_enters_vim_normal_and_keeps_focus() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            text_area = app.screen.query_one(_ConsoleInput)
            for key in "hi":
                await pilot.press(key)
            await pilot.press("escape")
            await pilot.pause()

            assert text_area.mode is Mode.NORMAL
            assert app.screen.focused is text_area

    asyncio.run(scenario())


def test_second_escape_blurs_out_of_the_console() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            text_area = app.screen.query_one(_ConsoleInput)
            await pilot.press("escape")  # insert -> normal, still focused
            await pilot.press("escape")  # normal -> blurred
            await pilot.pause()

            assert app.screen.focused is not text_area

    asyncio.run(scenario())


def test_vim_motions_work_in_normal_mode() -> None:
    """hjkl and friends come from VimTextArea itself; this just checks the
    integration didn't accidentally swallow them before they get there."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            text_area = app.screen.query_one(_ConsoleInput)
            text_area.load_text("abc")
            text_area.move_cursor((0, 0))
            await pilot.press("escape")  # -> NORMAL
            await pilot.pause()
            await pilot.press("l")  # move right one column
            await pilot.pause()
            assert text_area.cursor_location == (0, 1)

    asyncio.run(scenario())


def test_shift_enter_still_executes_after_the_vim_swap() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            text_area = app.screen.query_one(_ConsoleInput)
            text_area.load_text("1 + 1")
            await pilot.press("shift+enter")
            await asyncio.sleep(0.3)
            await pilot.pause()

            console = app.screen.query_one(IyzeeConsole)
            assert console.shell.shell.execution_count >= 1

    asyncio.run(scenario())
