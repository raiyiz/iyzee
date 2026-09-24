"""Tests for the Connect page: the connect/disconnect worker flow, its guards,
and what it shows.

INSTRUMENTS is monkeypatched to a single fake spec (see ``helpers.connect_app``)
so these never touch real hardware or the real drivers — only
``connect.INSTRUMENTS`` (what the page iterates) and ``app.handles`` /
``app.instrument_locks`` (what it mutates) are exercised.
"""

from __future__ import annotations

import time

import pytest
from helpers import (
    FakeHandle,
    async_test,
    connect_app,
    enter_on_first_row,
    notifications,
    plain,
    wait_until,
)
from rich.text import Text
from textual.pilot import Pilot
from textual.widgets import DataTable, Static

from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL


async def _row_is(pilot: Pilot, table: DataTable, status: str) -> None:
    await wait_until(pilot, lambda: table.get_cell("fake", STATUS_COL) == status)


@async_test
async def test_connecting_a_row_updates_table_handles_hint_and_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idn = "KEYSIGHT TECHNOLOGIES,N9020B,MY57120001,A.29.05"
    handle = FakeHandle(probe=idn)
    app = connect_app(monkeypatch, handle)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        hint = app.query_one("#connect-hint", Static)
        assert "connect Fake" in plain(hint) and "disconnect" not in plain(hint)

        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "connected")
        await pilot.pause()
        assert app.handles["fake"] is handle and handle.connected
        assert table.get_cell("fake", DETAIL_COL) == idn
        # The column must widen to show the identification string (it used to
        # keep the width of its first content, "-", and clip it to a few characters).
        width = table.columns[DETAIL_COL].get_render_width(table)  # type: ignore[index]
        assert width >= len(idn), f"Detail column is {width} wide, string is {len(idn)}"
        assert "disconnect Fake" in plain(hint)


@pytest.mark.parametrize(
    "message",
    [
        "bench unreachable",
        "could not open [/dev/ttyUSB0]",  # [/...] is a *closing tag*: MarkupError killed the app
        "timeout [nan, nan] on read",  # lowercase [...] is a tag: silently swallowed
        "[Errno 113] No route to host",
    ],
)
@async_test
async def test_connect_error_is_shown_verbatim_and_leaves_no_handle(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    app = connect_app(monkeypatch, FakeHandle(connect_error=message))
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "error")
        await pilot.pause(0.1)
        app.export_screenshot()  # force a real render of the table
        # DataTable parses str cells as markup; what it would display is
        # exactly the original message, brackets and all.
        assert Text.from_markup(table.get_cell("fake", DETAIL_COL)).plain == message
        assert f"Fake: {message}" in notifications(app), "the toast carries the raw text"
        # A failed connect must not leave a handle behind: the instrument still
        # reads as "not connected" to the rest of the app (Sweep guards, lab).
        assert "fake" not in app.handles
        assert app.is_running


@async_test
async def test_a_failed_probe_closes_the_half_open_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() succeeded but probe() failed: nothing will ever hold a
    reference to that handle, so the connect path has to close it."""
    handle = FakeHandle(probe_error="no answer")
    app = connect_app(monkeypatch, handle)
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "error")
        assert (handle.connect_calls, handle.disconnect_calls) == (1, 1)
        assert "fake" not in app.handles
        assert table.get_cell("fake", DETAIL_COL) == "no answer"


@async_test
async def test_enter_on_a_connecting_row_does_not_open_the_device_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = FakeHandle(connect_delay=0.4), FakeHandle(connect_delay=0.4)
    app = connect_app(monkeypatch, first, second)
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await pilot.pause(0.1)  # the first connect is now in flight
        await pilot.press("enter")  # impatient second press
        await _row_is(pilot, table, "connected")
        assert first.connect_calls == 1
        assert second.connect_calls == 0, "second Enter must be ignored while busy"
        assert app.handles["fake"] is first


@async_test
async def test_disconnect_requires_a_fresh_second_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = FakeHandle()
    app = connect_app(monkeypatch, handle)
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "connected")
        await wait_until(pilot, lambda: not screen._busy)  # the row is usable again

        await pilot.press("enter")  # first Enter arms it...
        await pilot.pause(0.2)
        assert "fake" in app.handles and handle.disconnect_calls == 0
        assert "Press Enter again to disconnect Fake." in notifications(app)

        # An expired confirmation must not be enough to disconnect.
        assert screen._armed is not None
        screen._armed = (screen._armed[0], time.monotonic() - 1)  # force the confirmation window to expire
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert "fake" in app.handles and handle.disconnect_calls == 0

        # A genuinely fresh second Enter does disconnect.
        await pilot.press("enter")
        await _row_is(pilot, table, "disconnected")
        assert handle.disconnect_calls == 1 and "fake" not in app.handles


@async_test
async def test_disconnect_is_refused_while_a_sweep_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = FakeHandle()
    app = connect_app(monkeypatch, handle)
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "connected")
        app.sweep_running = True

        await pilot.press("enter", "enter")
        await pilot.pause(0.2)
        assert handle.disconnect_calls == 0 and "fake" in app.handles
        assert any("sweep is running" in m for m in notifications(app))
