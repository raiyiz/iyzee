"""The console's Vim behaviour through the real app: IPython's prompt, the virtual terminal, the keys.

These are the regressions found by driving ``main``: Escape took ~1.4 s to leave insert mode, a
quick ``Esc d d`` was read as an Alt chord, the keyboard could not leave the console, and ``:``
commands failed while the console was current. One app per test, because starting one is the cost.
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


def session(app: IyzeeApp) -> IPythonSession:
    current = app.console_session
    assert current is not None, "the console has not been composed yet"
    return current


def prompt_text(app: IyzeeApp) -> str:
    prompt = session(app).shell.pt_app
    assert prompt is not None
    return prompt.default_buffer.text


async def mode(pilot: Pilot[Any], app: IyzeeApp, want: str) -> None:
    await wait_until(pilot, lambda: session(app).vi_mode == want)


async def type_text(pilot: Pilot[Any], text: str) -> None:
    await pilot.press(*("space" if c == " " else c for c in text))


@async_test
async def test_the_prompt_edits_like_vim() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("i")
        await mode(pilot, app, "insert")
        await type_text(pilot, "abc")
        started = time.monotonic()
        await pilot.press("escape")
        # Used to take ~1.4 s (prompt_toolkit's defaults); the bound is generous for slow CI.
        await wait_until(pilot, lambda: session(app).vi_mode == "normal", timeout=0.6)
        assert time.monotonic() - started < 0.6
        view = app.query_one(TerminalView)
        assert view._cursor_style().reverse  # a block in normal mode...
        await pilot.press("R")
        await mode(pilot, app, "replace")
        replace = view._cursor_style()
        assert replace.reverse and replace.underline  # ...both in replace mode...
        await pilot.press("escape")
        await mode(pilot, app, "normal")
        await pilot.press("i")
        await mode(pilot, app, "insert")
        assert view._cursor_style().underline  # ...an underline in insert mode
        await pilot.press("ctrl+u", "ctrl+u")  # clear the line (insert-mode kill-line)

        # `Esc d d` used to be read as Alt-d: still in insert mode, text 'foo bar bazd'.
        await type_text(pilot, "foo bar baz")
        await pilot.press("escape", "d", "d")
        await wait_until(pilot, lambda: prompt_text(app) == "")
        await mode(pilot, app, "normal")

        # `.` repeats the change; yanks (but not deletes) reach the system clipboard.
        await pilot.press("i")
        await type_text(pilot, "one two three four")
        await pilot.press("escape", "0", "y", "w")
        await wait_until(pilot, lambda: app.clipboard == "one ")
        await pilot.press("d", "w", "full_stop", "full_stop")
        await wait_until(pilot, lambda: prompt_text(app) == "four")
        await pilot.pause(0.1)
        assert app.clipboard == "one "  # the deletes did not replace the yank


@async_test
async def test_escape_and_colon_hand_the_keyboard_back_and_forth() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        page = lambda: app.query_one(ContentSwitcher).current  # noqa: E731
        bar = app.query_one(CommandBar)
        await pilot.press("i")
        await mode(pilot, app, "insert")
        await pilot.press("colon")  # in insert mode `:` is just a character
        await wait_until(pilot, lambda: prompt_text(app) == ":")
        assert not bar.is_open
        await pilot.press("backspace", "escape")  # insert -> normal: still the terminal's
        await mode(pilot, app, "normal")
        assert isinstance(app.focused, TerminalView)

        await pilot.press("escape")  # normal and at rest: the app's
        await wait_until(pilot, lambda: app.focused is None)
        await pilot.press("o")  # letters navigate again
        await wait_until(pilot, lambda: page() == "scope")
        await pilot.press("i")
        await wait_until(
            pilot, lambda: page() == "console" and isinstance(app.focused, TerminalView)
        )
        await pilot.press("escape")  # on the console page without the keyboard, `i` takes you back
        await wait_until(pilot, lambda: app.focused is None)
        await pilot.press("i")
        await wait_until(pilot, lambda: isinstance(app.focused, TerminalView))

        await pilot.press("d")  # an operator is pending: Escape cancels it and must not also leave
        await wait_until(pilot, lambda: not session(app).at_rest)
        await pilot.press("escape")
        await wait_until(pilot, lambda: session(app).at_rest)
        assert isinstance(app.focused, TerminalView)

        await pilot.press("colon")  # at rest: the shared command line, and page commands work
        await wait_until(pilot, lambda: bar.is_open)
        await pilot.press(*"clear", "enter")  # a console command; the bar hands the keyboard back
        await wait_until(pilot, lambda: not bar.is_open)
        assert isinstance(app.focused, TerminalView)
        await pilot.press("colon")
        await wait_until(pilot, lambda: bar.is_open)
        await pilot.press(*"power", "enter")
        await wait_until(pilot, lambda: page() == "power")  # raised WrongType before
        await pilot.press("i")
        await wait_until(pilot, lambda: isinstance(app.focused, TerminalView))

        # While a cell runs there is no prompt to be half-way through, so Escape leaves.
        shell = session(app)
        await pilot.press("i")  # the prompt was left in normal mode
        await mode(pilot, app, "insert")
        shell.send(b"import time; [time.sleep(0.05) for _ in range(600)]\r")
        await wait_until(pilot, lambda: shell.executing)
        await pilot.press("escape")
        await wait_until(pilot, lambda: app.focused is None)
        shell.interrupt()  # short sleeps: the async exception lands between them
        await wait_until(pilot, lambda: not shell.executing)
