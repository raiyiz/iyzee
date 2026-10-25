"""Tests for the Connect page: the connect/disconnect worker flow, its guards,
and what it shows.

INSTRUMENTS is monkeypatched to a single fake spec (see ``helpers.connect_app``)
so these never touch real hardware or the real drivers — only
``connect.INSTRUMENTS`` (what the page iterates) and ``app.handles``/the
fake handle's own ``.lock`` (what it mutates and acquires) are exercised.
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

from iyzee.lab import InstrumentSpec
from iyzee.tui import app as app_mod
from iyzee.tui.screens import connect as connect_mod
from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL, ConnectScreen


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

        _, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "connected")
        await pilot.pause()
        assert app.handles["fake"] is handle and handle.connected
        assert table.get_cell("fake", DETAIL_COL) == idn
        # The column must widen to show the identification string (it used to
        # keep the width of its first content, "-", and clip it to a few characters).
        width = table.columns[DETAIL_COL].get_render_width(table)  # type: ignore[index]
        assert width >= len(idn), f"Detail column is {width} wide, string is {len(idn)}"
        assert "disconnect Fake" in plain(hint)


@async_test
async def test_connect_errors_and_probe_failure_leave_no_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = ["bench unreachable", "could not open [/dev/ttyUSB0]"]
    failed_connects = [FakeHandle(connect_error=message) for message in messages]
    failed_probe = FakeHandle(probe_error="no answer")
    app = connect_app(monkeypatch, *failed_connects, failed_probe)

    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)

        for message in messages:
            await wait_until(
                pilot,
                lambda: Text.from_markup(table.get_cell("fake", DETAIL_COL)).plain == message,
            )
            await _row_is(pilot, table, "error")
            await wait_until(pilot, lambda: not screen._busy)
            # Rendering must keep bracketed driver text literal, not interpret it as markup.
            app.export_screenshot()
            assert Text.from_markup(table.get_cell("fake", DETAIL_COL)).plain == message
            assert f"Fake: {message}" in notifications(app), "the toast carries the raw text"
            assert "fake" not in app.handles
            assert app.is_running

            # Retry the same row with the next fake handle queued by connect_app.
            if message != messages[-1]:
                await pilot.press("enter")

        assert failed_connects[0].connect_calls == 1
        assert failed_connects[1].connect_calls == 1

        await pilot.press("enter")
        await wait_until(
            pilot,
            lambda: Text.from_markup(table.get_cell("fake", DETAIL_COL)).plain == "no answer",
        )
        await _row_is(pilot, table, "error")
        await wait_until(pilot, lambda: not screen._busy)
        assert (failed_probe.connect_calls, failed_probe.disconnect_calls) == (1, 1)
        assert "fake" not in app.handles
        assert table.get_cell("fake", DETAIL_COL) == "no answer"


@async_test
async def test_connect_row_guards_retries_and_safe_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = FakeHandle(connect_delay=0.4), FakeHandle(connect_delay=0.4)
    app = connect_app(monkeypatch, first, second)
    async with app.run_test() as pilot:
        screen, table = await enter_on_first_row(app, pilot)
        await _row_is(pilot, table, "connecting...")
        await pilot.press("enter")  # impatient second press must not start the second handle
        await _row_is(pilot, table, "connected")
        await wait_until(pilot, lambda: not screen._busy)
        assert first.connect_calls == 1
        assert second.connect_calls == 0
        assert app.handles["fake"] is first

        await pilot.press("enter")  # first Enter arms disconnect confirmation
        await wait_until(pilot, lambda: screen._armed is not None)
        assert "fake" in app.handles and first.disconnect_calls == 0
        assert "Press Enter again to disconnect Fake." in notifications(app)

        # An expired confirmation must only re-arm; a fresh second Enter disconnects.
        assert screen._armed is not None
        screen._armed = (screen._armed[0], time.monotonic() - 1)
        await pilot.press("enter")
        assert "fake" in app.handles and first.disconnect_calls == 0
        await pilot.press("enter")
        await _row_is(pilot, table, "disconnected")
        assert first.disconnect_calls == 1 and "fake" not in app.handles

        # The next attempt uses the second handle; a running sweep blocks disconnect.
        await pilot.press("enter")
        await _row_is(pilot, table, "connected")
        await wait_until(pilot, lambda: not screen._busy)
        assert app.handles["fake"] is second and second.connect_calls == 1
        app.sweep_running = True
        await pilot.press("enter", "enter")
        assert second.disconnect_calls == 0 and app.handles["fake"] is second
        assert any("sweep is running" in m for m in notifications(app))

    assert second.disconnect_calls == 1


@async_test
async def test_a_part_and_its_parent_keep_each_others_rows_in_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connecting the shutter connects the PSU it is a channel of; disconnecting the PSU
    takes the shutter with it. The rows of both must say so, not just the one acted on."""
    parent, part = FakeHandle(probe="supply ready"), FakeHandle(probe="shutter ready")
    specs = [
        InstrumentSpec("psu", "PSU", lambda: parent, short="PSU"),
        InstrumentSpec("shutter", "Shutter", lambda _psu: part, short="Sh", parent="psu"),
    ]
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", specs)
    monkeypatch.setattr(app_mod, "INSTRUMENTS", specs)
    app = app_mod.IyzeeApp()
    app.lab.specs = specs
    async with app.run_test() as pilot:
        screen = app.query_one(ConnectScreen)
        table = screen.query_one(DataTable)

        def status(key: str) -> str:
            return str(table.get_cell(key, "status"))

        screen.request_connect("shutter")
        await wait_until(pilot, lambda: status("shutter") == "connected")
        await wait_until(pilot, lambda: status("psu") == "connected")
        assert str(table.get_cell("psu", "detail")) == "supply ready"
        assert set(app.handles) == {"psu", "shutter"} and parent.connect_calls == 1

        screen.request_disconnect("psu")
        await wait_until(pilot, lambda: status("psu") == "disconnected")
        await wait_until(pilot, lambda: status("shutter") == "disconnected")
        assert app.handles == {} and part.disconnected and parent.disconnected
