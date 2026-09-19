"""Tests for ConnectScreen's connect/disconnect worker flow.

INSTRUMENTS is monkeypatched to a single fake spec so these never touch
real hardware or the real device drivers — only instruments.INSTRUMENTS
(what connect.py iterates) and app.handles / app.instrument_locks (what
it mutates) are exercised.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from textual.widgets import DataTable

from iyzee.tui import app as app_mod
from iyzee.tui.instruments import InstrumentSpec
from iyzee.tui.screens import connect as connect_mod
from iyzee.tui.screens.connect import DETAIL_COL, STATUS_COL, ConnectScreen


class _FakeHandle:
    """Controllable stand-in for a real InstrumentHandle."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.connected = False
        self.disconnected = False

    def connect(self) -> None:
        if self.fail:
            raise ConnectionError("bench unreachable")
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    def probe(self) -> str:
        return "fake ready"


def _fake_spec(*, fail: bool = False) -> InstrumentSpec:
    return InstrumentSpec(key="fake", label="Fake Instrument", make=lambda: _FakeHandle(fail=fail))


async def _wait_for_row_status(screen: ConnectScreen, key: str, *statuses: str) -> str:
    table = screen.query_one(DataTable)
    for _ in range(50):
        value = table.get_cell(key, STATUS_COL)
        if value in statuses:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError(f"row {key!r} never reached one of {statuses}; last saw {value!r}")


def test_connecting_a_row_updates_table_and_app_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [_fake_spec()])

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("c")
            await pilot.pause()
            screen = app.query_one(ConnectScreen)

            table = screen.query_one(DataTable)
            table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
            await pilot.press("enter")

            status = await _wait_for_row_status(screen, "fake", "connected", "error")
            assert status == "connected"
            assert "fake" in app.handles
            assert cast(_FakeHandle, app.handles["fake"]).connected is True
            assert table.get_cell("fake", DETAIL_COL) == "fake ready"

    asyncio.run(scenario())


def test_connect_failure_surfaces_as_error_row_without_crashing_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [_fake_spec(fail=True)])

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("c")
            await pilot.pause()
            screen = app.query_one(ConnectScreen)
            table = screen.query_one(DataTable)
            table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
            await pilot.press("enter")

            status = await _wait_for_row_status(screen, "fake", "connected", "error")
            assert status == "error"
            # A failed connect must not have left a handle behind — the
            # instrument should still read as "not connected" to the rest
            # of the app (Sweep screen's guards, LabProxy, etc.).
            assert "fake" not in app.handles

    asyncio.run(scenario())


def test_disconnecting_a_connected_row_clears_app_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connect_mod, "INSTRUMENTS", [_fake_spec()])

    async def scenario() -> None:
        app = app_mod.IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("c")
            await pilot.pause()
            screen = app.query_one(ConnectScreen)
            table = screen.query_one(DataTable)
            table.cursor_coordinate = table.cursor_coordinate._replace(row=0)
            await pilot.press("enter")
            await _wait_for_row_status(screen, "fake", "connected", "error")

            handle = cast(_FakeHandle, app.handles["fake"])
            # Same row again -> disconnect, which asks for confirmation: the
            # first Enter only arms it, the second (within a few seconds) does it.
            await pilot.press("enter")
            await pilot.press("enter")

            for _ in range(50):
                if handle.disconnected:
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("disconnect never completed")

            assert "fake" not in app.handles
            assert table.get_cell("fake", STATUS_COL) == "disconnected"

    asyncio.run(scenario())
