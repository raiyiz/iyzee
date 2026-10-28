"""Vim motions (j k gg G Ctrl+D Ctrl+U, counts) on tables, lists, dropdowns and logs."""

from __future__ import annotations

import pytest
from helpers import async_test, wait_until
from textual.app import App, ComposeResult
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RichLog,
)

from iyzee.tui.app import IyzeeApp
from iyzee.tui.command_bar import CommandBar
from iyzee.tui.motions import MOTIONS, move


@async_test
async def test_motions_and_counts_in_the_app_and_digits_still_type() -> None:
    app = IyzeeApp()
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        table.focus()
        await pilot.pause()
        assert table.row_count >= 4
        last = table.row_count - 1

        # j/k move the table's cursor, not the focus; G / gg / Ctrl+D / Ctrl+U jump.
        await pilot.press("j", "j")
        assert table.cursor_row == 2 and app.focused is table
        await pilot.press("k")
        assert table.cursor_row == 1
        await pilot.press("G")
        assert table.cursor_row == last
        await pilot.press("g", "g")
        assert table.cursor_row == 0
        await pilot.press("ctrl+d")
        assert table.cursor_row > 0
        await pilot.press("ctrl+u")
        assert table.cursor_row == 0

        # A lone g expires; the second one only re-arms, the third completes gg.
        await pilot.press("j", "j", "g")
        app._g_armed_until = 0.0
        await pilot.press("g")
        assert table.cursor_row == 2
        await pilot.press("g")
        assert table.cursor_row == 0

        # Counts repeat a motion or name the line for G / gg; they are used up and they expire.
        await pilot.press("2", "j")
        assert table.cursor_row == 2
        await pilot.press("3", "G")
        assert table.cursor_row == 2
        await pilot.press("9", "9", "G")
        assert table.cursor_row == last
        await pilot.press("2", "g", "g")
        assert table.cursor_row == 1
        await pilot.press("j")
        assert table.cursor_row == 2
        await pilot.press("3")
        app._count_until = 0.0
        await pilot.press("j")
        assert table.cursor_row == 3
        assert app.check_action("count_digit", (0,)) is False  # 0 only continues a count
        await pilot.press("1", "0")
        assert app._count == 10
        app._count = 0

        # Digits typed into fields and the console are text, not counts.
        await pilot.press("colon", "1", "2")
        assert app.query_one(CommandBar).query_one(Input).value == "12"
        await pilot.press("escape", "s")
        await pilot.pause()
        field = next(i for i in app.query("SweepScreen Input").results(Input) if i.display)
        field.value = ""
        field.focus()
        await pilot.pause()
        await pilot.press("4", "0", "2")
        assert field.value == "402" and app._count == 0
        await pilot.press("f4")
        await wait_until(
            pilot,
            lambda: app.console_session is not None and app.console_session.vi_mode == "insert",
        )
        await pilot.press("7", "8")
        prompt = app.console_session.shell.pt_app if app.console_session else None
        assert prompt is not None
        await wait_until(pilot, lambda: prompt.default_buffer.text == "78")

        # A log scrolls; a button has nothing to scroll, so j moves the focus on.
        await pilot.press("escape", "escape")
        await wait_until(pilot, lambda: app.focused is None)
        await pilot.press("l")
        await pilot.pause()
        log = app.query_one("#log-view", RichLog)
        log.auto_scroll = False  # else a deferred scroll-to-end races with the test's scrolling
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
        await pilot.press("j")
        assert app.focused is not button


class _Widgets(App[None]):
    def compose(self) -> ComposeResult:
        yield ListView(*(ListItem(Label(str(n))) for n in range(5)))
        yield OptionList("a", "b", "c")
        yield Button("ok")


@async_test
async def test_move_drives_lists_and_dropdowns_and_declines_buttons() -> None:
    app = _Widgets()
    async with app.run_test() as pilot:
        await pilot.pause()
        listing, options, button = (
            app.query_one(ListView),
            app.query_one(OptionList),
            app.query_one(Button),
        )

        listing.index = 0
        assert move(listing, "down") and listing.index == 1
        assert move(listing, "bottom") and listing.index == 4
        assert move(listing, "up") and listing.index == 3
        assert move(listing, "top") and listing.index == 0

        options.highlighted = 0
        assert move(options, "down") and options.highlighted == 1
        assert move(options, "bottom") and options.highlighted == 2

        assert not move(button, "down") and not move(button, "up")  # the app moves focus instead
        assert not move(None, "down")
        with pytest.raises(ValueError, match="unknown motion"):
            move(None, "sideways")
        assert set(MOTIONS) == {"down", "up", "top", "bottom", "half_down", "half_up"}
