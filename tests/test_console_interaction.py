"""Console interaction quality-of-life features, exercised through the real
ConsoleScreen/IyzeeIPython wiring the same way test_console_screen.py does:
selectable Tab-completion, the running/interrupt state machine, Ctrl+L
clear, and the ANSI-traceback rendering fix.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from textual.widgets import OptionList, RichLog

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import IyzeeConsole, _ConsoleInput


async def _wait_until(
    predicate: Callable[[], bool], *, pilot, timeout: float = 6.0, step: float = 0.1
) -> bool:
    """Poll `predicate()` via repeated `pilot.pause()`s until it's true or
    `timeout` elapses. Cell execution runs in a background worker thread,
    so a single fixed pause is either too short (flaky) or wastefully long
    (slow suite) — this settles for whichever comes first, same idea as
    the fixed `pilot.pause(0.3)` calls elsewhere in the suite but bounded
    for the handful of cases here that can legitimately take a moment.
    """
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await pilot.pause(step)
        elapsed += step
    return predicate()


def test_tab_completion_is_selectable_and_replaces_the_right_span() -> None:
    """Regression test for a real bug behind the old completion UI: the
    replacement span used a whitespace-based "start of token" heuristic
    that doesn't treat `.` as a boundary, so accepting ".handles" out of
    "lab.<Tab>" replaced the *whole* "lab." and left just ".handles" in
    the buffer instead of "lab.handles". Fixed by using the prefix
    IPython's own completer reports instead of guessing at word
    boundaries.
    """

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            text_area.load_text("lab.")
            text_area.move_cursor((0, 4))
            await pilot.press("tab")
            await pilot.pause()

            options = app.screen.query_one("#console-completions", OptionList)
            assert console.completions_visible
            assert options.option_count > 1
            assert options.highlighted == 0

            await pilot.press("down")
            await pilot.pause()
            assert options.highlighted == 1

            await pilot.press("enter")
            await pilot.pause()

            assert not console.completions_visible
            # Whichever candidate ended up highlighted, accepting it must
            # produce a full "lab.xxx" replacement -- never a bare ".xxx"
            # with "lab" chopped off.
            assert text_area.text.strip().startswith("lab.")
            assert not text_area.text.strip().startswith(".")

    asyncio.run(scenario())


def test_tab_completion_accepts_correctly_with_a_partial_prefix_typed() -> None:
    """"lab.ha<Tab>" -> accept should give "lab.handles", not
    "lab.hahandles" or ".handles" -- the case that most commonly triggers
    the bug fixed above, since it's how completion is normally used (part
    way through typing a name, not immediately after the dot)."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)

            text_area.load_text("lab.ha")
            text_area.move_cursor((0, 6))
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert text_area.text.strip() == "lab.handles"

    asyncio.run(scenario())


def test_tab_completion_escape_closes_without_editing_text() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            text_area.load_text("lab.")
            text_area.move_cursor((0, 4))
            await pilot.press("tab")
            await pilot.pause()
            assert console.completions_visible

            await pilot.press("escape")
            await pilot.pause()

            assert not console.completions_visible
            assert text_area.text.strip() == "lab."

    asyncio.run(scenario())


def test_second_shift_enter_while_running_does_not_cancel_or_restart() -> None:
    """A cell that's still running used to be silently cancelled and
    replaced by whatever Shift+Enter was pressed next (the worker is
    `exclusive=True`), with no feedback that anything happened. Regression
    test that a second Shift+Enter is now a no-op instead: the first cell
    keeps running to completion and the second is never executed."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            text_area.load_text("import time\ntime.sleep(1)\n'first cell done'")
            await pilot.press("shift+enter")
            await pilot.pause(0.2)
            assert console._executing

            text_area.load_text("'second cell'")
            await pilot.press("shift+enter")
            await pilot.pause(0.2)

            # Still running the first cell -- the second one never started,
            # and critically wasn't silently discarded either: it's still
            # sitting right there in the input for the user to run once
            # the first one finishes.
            assert console._executing
            assert text_area.text.strip() == "'second cell'"

            finished = await _wait_until(lambda: not console._executing, pilot=pilot)
            assert finished

            log = app.screen.query_one("#console-output", RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "second cell" not in rendered

    asyncio.run(scenario())


def test_ctrl_c_interrupts_a_running_cell_and_console_stays_usable() -> None:
    """Ctrl+C should actually stop a running cell (by raising
    KeyboardInterrupt in its worker thread) rather than doing nothing --
    and afterwards the console must be usable again, not stuck showing
    "running" forever."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            # A pure-Python loop, not time.sleep(): CPython only checks
            # for an asynchronously-injected exception at bytecode
            # boundaries, and a single blocking C call like time.sleep()
            # doesn't hit one until it returns on its own. A loop is the
            # case that actually gets interrupted promptly.
            text_area.load_text("while True:\n    pass")
            await pilot.press("shift+enter")
            await pilot.pause(0.2)
            assert console._executing

            await pilot.press("ctrl+c")
            stopped = await _wait_until(lambda: not console._executing, pilot=pilot, timeout=5.0)
            assert stopped

            # And the console is still usable afterwards -- this is the
            # main risk with any thread-interrupt trick: leaving things in
            # a state where nothing can run again.
            text_area.load_text("40 + 2")
            await pilot.press("shift+enter")
            finished = await _wait_until(lambda: not console._executing, pilot=pilot)
            assert finished
            log = app.screen.query_one("#console-output", RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "42" in rendered

    asyncio.run(scenario())


def test_ctrl_c_without_a_running_cell_is_a_harmless_no_op() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            console = app.screen.query_one(IyzeeConsole)
            assert not console._executing

            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert not console._executing

    asyncio.run(scenario())


def test_ctrl_l_clears_the_output_log_without_touching_history() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            text_area.load_text("1 + 1")
            await pilot.press("shift+enter")
            await _wait_until(lambda: not console._executing, pilot=pilot)

            log = app.screen.query_one("#console-output", RichLog)
            assert len(log.lines) > 0

            await pilot.press("ctrl+l")
            await pilot.pause()
            assert len(log.lines) == 0

            # History (Ctrl+P) survives the clear -- it's a separate
            # concept from the visible transcript.
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert text_area.text.strip() == "1 + 1"

    asyncio.run(scenario())


def test_traceback_output_is_colored_not_raw_ansi_escape_bytes() -> None:
    """`_finish_execution` used to push IPython's ANSI-colored traceback
    text through `rich.markup.escape()`, which only escapes Rich markup
    (`[...]`) syntax -- raw ANSI escape bytes went straight into the log
    as literal "\\x1b[31m..." noise instead of being rendered as color.
    Regression test that the escape bytes are gone from the rendered
    output (they've been decoded into real styling via `Text.from_ansi`
    instead)."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(_ConsoleInput)
            console = app.screen.query_one(IyzeeConsole)

            text_area.load_text("1 / 0")
            await pilot.press("shift+enter")
            await _wait_until(lambda: not console._executing, pilot=pilot)

            log = app.screen.query_one("#console-output", RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "ZeroDivisionError" in rendered
            assert "\x1b[" not in rendered

    asyncio.run(scenario())
