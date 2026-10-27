"""The console's Vim behaviour through the real app: IPython's prompt, the virtual terminal, the keys.

These are the regressions found by driving ``main``: Escape took ~1.4 s to leave insert mode, a
quick ``Esc d d`` was read as an Alt chord, the keyboard could not leave the console, and ``:``
commands failed while the console was current.
"""

from __future__ import annotations

import time
from typing import Any

from helpers import async_test, wait_until
from textual.pilot import Pilot
from textual.widgets import ContentSwitcher

from iyzee.tui.app import IyzeeApp
from iyzee.tui.command_bar import CommandBar
from iyzee.tui.ipython_session import IPythonSession
from iyzee.tui.terminal_view import TerminalView


async def open_console(pilot: Pilot[Any], app: IyzeeApp) -> None:
    await pilot.press("i")
    await wait_until(pilot, lambda: session(app).vi_mode == "insert")


async def type_text(pilot: Pilot[Any], text: str) -> None:
    await pilot.press(*("space" if c == " " else c for c in text))


def session(app: IyzeeApp) -> IPythonSession:
    current = app.console_session
    assert current is not None, "the console has not been composed yet"
    return current


def prompt_text(app: IyzeeApp) -> str:
    prompt = session(app).shell.pt_app
    assert prompt is not None
    return prompt.default_buffer.text


def page(app: IyzeeApp) -> str | None:
    return app.query_one(ContentSwitcher).current


@async_test
async def test_escape_leaves_insert_mode_at_once() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await type_text(pilot, "abc")
        started = time.monotonic()
        await pilot.press("escape")
        # Used to take ~1.4 s (prompt_toolkit's defaults); the bound is generous for slow CI.
        await wait_until(pilot, lambda: session(app).vi_mode == "normal", timeout=0.6)
        assert time.monotonic() - started < 0.6


@async_test
async def test_a_quick_escape_then_letters_is_vim_not_an_alt_chord() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await type_text(pilot, "foo bar baz")
        # `Esc d d` used to be read as Alt-d: still in insert mode, text 'foo bar bazd'.
        await pilot.press("escape", "d", "d")
        await wait_until(pilot, lambda: prompt_text(app) == "")
        assert session(app).vi_mode == "normal"

        await type_text(pilot, "ix")  # a stray `i` in normal mode starts inserting again
        await pilot.press("escape", "u")  # `Esc u` used to uppercase a word and stay in insert mode
        await wait_until(pilot, lambda: session(app).vi_mode == "normal")
        assert prompt_text(app) == ""


@async_test
async def test_escape_twice_returns_the_keyboard_to_the_app_and_i_gives_it_back() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await pilot.press("escape")  # insert -> normal: still the terminal's
        await wait_until(pilot, lambda: session(app).vi_mode == "normal")
        assert isinstance(app.focused, TerminalView)

        await pilot.press("escape")  # normal and at rest: the app's
        await wait_until(pilot, lambda: app.focused is None)

        await pilot.press("o")  # letters navigate again
        await wait_until(pilot, lambda: page(app) == "scope")
        await pilot.press("i")
        await wait_until(
            pilot, lambda: page(app) == "console" and isinstance(app.focused, TerminalView)
        )

        await pilot.press("escape")  # already on the console page, but without the keyboard:
        await wait_until(pilot, lambda: app.focused is None)
        await pilot.press("i")  # ...`i` is how you go back to typing
        await wait_until(pilot, lambda: isinstance(app.focused, TerminalView))


@async_test
async def test_escape_with_something_half_typed_stays_in_the_prompt() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await pilot.press("escape")
        await wait_until(pilot, lambda: session(app).at_rest)
        await pilot.press("d")  # an operator is pending
        await wait_until(pilot, lambda: not session(app).at_rest)
        await pilot.press("escape")  # cancels the operator; it must not also leave
        await wait_until(pilot, lambda: session(app).at_rest)
        assert isinstance(app.focused, TerminalView)


@async_test
async def test_escape_leaves_the_console_while_a_cell_is_running() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        shell = session(app)
        # Short sleeps, so the interrupt (an async exception) lands between them.
        shell.send(b"import time; [time.sleep(0.05) for _ in range(600)]\r")
        await wait_until(pilot, lambda: shell.executing)
        assert shell.at_rest  # no prompt to be half-way through
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.focused is None)
        shell.interrupt()
        await wait_until(pilot, lambda: not shell.executing)


@async_test
async def test_colon_commands_work_from_the_console() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        # In insert mode `:` is just a character for the prompt.
        await pilot.press("colon")
        await wait_until(pilot, lambda: prompt_text(app) == ":")
        assert not app.query_one(CommandBar).is_open
        await pilot.press("backspace", "escape")
        await wait_until(pilot, lambda: session(app).at_rest)

        await pilot.press("colon")  # normal mode at rest: the app's command line
        await wait_until(pilot, lambda: app.query_one(CommandBar).is_open)
        await pilot.press(*"power", "enter")
        await wait_until(pilot, lambda: page(app) == "power")  # raised WrongType before


@async_test
async def test_colon_clear_is_a_console_command() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        assert "clear" in app.command_registry().names
        await pilot.press("escape", "colon")
        await wait_until(pilot, lambda: app.query_one(CommandBar).is_open)
        await pilot.press(*"clear", "enter")
        await wait_until(pilot, lambda: not app.query_one(CommandBar).is_open)
        assert isinstance(app.focused, TerminalView)  # the bar hands the keyboard back


@async_test
async def test_the_cursor_shows_the_vi_mode() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        view = app.query_one(TerminalView)
        insert = view._cursor_style()
        assert insert.underline and insert.bold and not insert.reverse

        await pilot.press("escape")
        await wait_until(pilot, lambda: session(app).vi_mode == "normal")
        normal = view._cursor_style()
        assert normal.reverse and not normal.underline

        await pilot.press("R")
        await wait_until(pilot, lambda: session(app).vi_mode == "replace")
        replace = view._cursor_style()
        assert replace.reverse and replace.underline

        await pilot.press("escape", "escape")  # replace -> normal -> out of the terminal
        await wait_until(pilot, lambda: app.focused is None)
        assert view._cursor_style().dim  # unfocused


@async_test
async def test_a_yank_reaches_the_clipboard_but_a_delete_does_not() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await type_text(pilot, "foo bar")
        await pilot.press("escape", "0", "y", "w")
        await wait_until(pilot, lambda: app.clipboard == "foo ")

        await pilot.press("d", "w")  # a delete must not replace what was yanked
        await wait_until(pilot, lambda: prompt_text(app) == "bar")
        await pilot.pause(0.1)
        assert app.clipboard == "foo "


@async_test
async def test_dot_repeats_inside_the_real_prompt() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await type_text(pilot, "one two three four")
        await pilot.press("escape", "0", "d", "w", "full_stop", "full_stop")
        await wait_until(pilot, lambda: prompt_text(app) == "four")


@async_test
async def test_q_asks_first_and_qq_quits_at_once() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await open_console(pilot, app)
        await pilot.press("escape", "escape")
        await wait_until(pilot, lambda: app.focused is None)
        await pilot.press("colon")
        await wait_until(pilot, lambda: app.query_one(CommandBar).is_open)
        await pilot.press("q", "enter")
        await pilot.pause(0.2)
        assert app.is_running  # `:q` only asks, like the q key
        await pilot.press("colon")
        await wait_until(pilot, lambda: app.query_one(CommandBar).is_open)
        await pilot.press("q", "q", "enter")
        await wait_until(pilot, lambda: not app.is_running)
