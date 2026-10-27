"""Vim motions (j k gg G Ctrl+D Ctrl+U) on tables, lists, dropdowns and logs."""

from __future__ import annotations

import pytest
from helpers import async_test, wait_until
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Label, ListItem, ListView, OptionList, RichLog

from iyzee.tui.app import IyzeeApp
from iyzee.tui.ipython_session import IPythonSession
from iyzee.tui.motions import MOTIONS, move


@async_test
async def test_j_and_k_move_a_table_cursor_instead_of_the_focus() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        assert table.row_count >= 3
        start = table.cursor_row

        await pilot.press("j")
        assert table.cursor_row == start + 1 and app.focused is table
        await pilot.press("k")
        assert table.cursor_row == start

        await pilot.press("G")
        assert table.cursor_row == table.row_count - 1
        await pilot.press("g", "g")
        assert table.cursor_row == 0

        await pilot.press("ctrl+d")
        down = table.cursor_row
        assert down > 0
        await pilot.press("ctrl+u")
        assert table.cursor_row < down
        assert app.focused is table


@async_test
async def test_a_lone_g_expires_and_does_not_jump() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("j", "j")
        row = table.cursor_row
        assert row >= 1
        await pilot.press("g")
        app._g_armed_until = 0.0  # the moment for the second g has passed
        await pilot.press("g")
        assert table.cursor_row == row  # the second g only re-armed it
        await pilot.press("g")
        assert table.cursor_row == 0  # ...and now it is gg


@async_test
async def test_motions_scroll_a_log_and_j_k_still_move_focus_elsewhere() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        log = app.query_one("#log-view", RichLog)
        log.auto_scroll = False  # else a deferred scroll-to-end races with the test's own scrolling
        for n in range(200):
            log.write(f"line {n}")
        log.focus()
        await wait_until(pilot, lambda: log.max_scroll_y > 0)
        log.scroll_home(animate=False)
        await pilot.pause()

        await pilot.press("j")
        await wait_until(pilot, lambda: log.scroll_y == 1)
        await pilot.press("G")
        await wait_until(pilot, lambda: log.scroll_y == log.max_scroll_y)
        await pilot.press("ctrl+u")
        await wait_until(pilot, lambda: 0 < log.scroll_y < log.max_scroll_y)
        await pilot.press("g", "g")
        await wait_until(pilot, lambda: log.scroll_y == 0)

        button = app.query_one("#log-refresh", Button)
        button.focus()
        await pilot.pause()
        await pilot.press("j")  # a button has no cursor and nothing to scroll: focus moves on
        assert app.focused is not button


class _Widgets(App[None]):
    def compose(self) -> ComposeResult:
        yield ListView(*(ListItem(Label(str(n))) for n in range(5)), id="list")
        yield OptionList("a", "b", "c", id="options")
        yield Button("ok", id="button")


@async_test
async def test_move_drives_lists_and_dropdowns_and_declines_buttons() -> None:
    app = _Widgets()
    async with app.run_test() as pilot:
        await pilot.pause()
        listing = app.query_one(ListView)
        options = app.query_one(OptionList)
        button = app.query_one(Button)

        listing.index = 0
        assert move(listing, "down") and listing.index == 1
        assert move(listing, "bottom") and listing.index == 4
        assert move(listing, "up") and listing.index == 3
        assert move(listing, "top") and listing.index == 0

        options.highlighted = 0
        assert move(options, "down") and options.highlighted == 1
        assert move(options, "bottom") and options.highlighted == 2
        assert move(options, "top") and options.highlighted == 0

        assert not move(button, "down")  # the app then falls back to moving focus
        assert not move(button, "up")
        assert not move(None, "down")


def test_an_unknown_motion_is_an_error_not_a_silent_no_op() -> None:
    with pytest.raises(ValueError, match="unknown motion"):
        move(None, "sideways")
    assert set(MOTIONS) == {"down", "up", "top", "bottom", "half_down", "half_up"}


@async_test
async def test_counts_repeat_motions_and_name_a_line_for_G_and_gg() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        assert table.row_count >= 4

        await pilot.press("2", "j")
        assert table.cursor_row == 2
        await pilot.press("1", "k")
        assert table.cursor_row == 1
        await pilot.press("3", "G")  # line 3 (1-based)
        assert table.cursor_row == 2
        await pilot.press("9", "9", "G")  # clamped to the last line
        assert table.cursor_row == table.row_count - 1
        await pilot.press("2", "g", "g")
        assert table.cursor_row == 1
        await pilot.press("j")  # the count was used up
        assert table.cursor_row == 2


@async_test
async def test_a_count_expires_and_zero_alone_is_not_taken() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("3")
        app._count_until = 0.0  # too long ago
        await pilot.press("j")
        assert table.cursor_row == 1  # one step, not three
        assert app.check_action("count_digit", (0,)) is False  # no count under way
        await pilot.press("1", "0")
        assert app._count == 10  # but 0 continues one


def console_session(app: IyzeeApp) -> IPythonSession:
    current = app.console_session
    assert current is not None
    return current


@async_test
async def test_digits_typed_into_fields_and_the_console_are_text_not_counts() -> None:
    from textual.widgets import Input

    from iyzee.tui.command_bar import CommandBar

    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        # The command line.
        await pilot.press("colon", "1", "2")
        bar_input = app.query_one(CommandBar).query_one(Input)
        assert bar_input.value == "12"
        await pilot.press("escape")

        # A form field on the Sweep page.
        await pilot.press("s")
        await pilot.pause()
        field = next(i for i in app.query("SweepScreen Input").results(Input) if i.display)
        field.value = ""
        field.focus()
        await pilot.pause()
        await pilot.press("4", "0", "2")
        assert field.value == "402"
        assert app._count == 0

        # The console prompt (the terminal takes every key while it has the keyboard).
        await pilot.press("f4")
        await wait_until(pilot, lambda: console_session(app).vi_mode == "insert")
        await pilot.press("7", "8")
        prompt = console_session(app).shell.pt_app
        assert prompt is not None
        await wait_until(pilot, lambda: prompt.default_buffer.text == "78")
        assert app._count == 0
