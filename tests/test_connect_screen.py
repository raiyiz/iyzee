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
async def test_a_failed_probe_a_double_enter_and_a_refused_disconnect_leave_consistent_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # -- disconnect requires a fresh second enter
    async def _disconnect_requires_a_fresh_second_enter():
        handle = FakeHandle()
        app = connect_app(monkeypatch, handle)
        async with app.run_test() as pilot:
            screen, table = await enter_on_first_row(app, pilot)
            await _row_is(pilot, table, "connected")
            await wait_until(pilot, lambda: not screen._busy)  # the row is usable again

            await pilot.press("enter")  # first Enter arms it...
            await wait_until(pilot, lambda: screen._armed is not None)
            assert "fake" in app.handles and handle.disconnect_calls == 0
            assert "Press Enter again to disconnect Fake." in notifications(app)

            # An expired confirmation must not be enough to disconnect.
            assert screen._armed is not None
            screen._armed = (
                screen._armed[0],
                time.monotonic() - 1,
            )  # force the confirmation window to expire
            await pilot.press("enter")
            assert "fake" in app.handles and handle.disconnect_calls == 0

            # A genuinely fresh second Enter does disconnect.
            await pilot.press("enter")
            await _row_is(pilot, table, "disconnected")
            assert handle.disconnect_calls == 1 and "fake" not in app.handles

    await _disconnect_requires_a_fresh_second_enter()

    # -- disconnect is refused while a sweep is running
    async def _disconnect_is_refused_while_a_sweep_is_r():
        handle = FakeHandle()
        app = connect_app(monkeypatch, handle)
        async with app.run_test() as pilot:
            _, table = await enter_on_first_row(app, pilot)
            await _row_is(pilot, table, "connected")
            app.sweep_running = True

            await pilot.press("enter", "enter")
            assert handle.disconnect_calls == 0 and "fake" in app.handles
            assert any("sweep is running" in m for m in notifications(app))

    await _disconnect_is_refused_while_a_sweep_is_r()


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
