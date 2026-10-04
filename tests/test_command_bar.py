from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FakeHandle, async_test, connect_app, notifications, wait_until
from textual.widgets import ContentSwitcher, DataTable, Input

from iyzee.tui import app as app_mod
from iyzee.tui.command_bar import CommandBar, CommandInput
from iyzee.tui.screens import results as results_mod


@pytest.fixture(autouse=True)
def quiet_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(results_mod, "_DATA_ROOT", tmp_path)  # never read the real data dir


async def run(pilot, line: str) -> None:
    await pilot.press(":")
    await pilot.press(*line)
    await pilot.press("enter")
    await pilot.pause()


@async_test
async def test_colon_opens_the_bar_and_escape_closes_it_returning_focus():
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        before = app.focused
        await pilot.press(":")
        bar = app.query_one(CommandBar)
        assert bar.is_open and isinstance(app.focused, CommandInput)

        await pilot.press("escape")
        assert not bar.is_open and app.focused is before


@async_test
async def test_a_page_name_switches_pages_and_q_quits():
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "results")
        assert app.query_one(ContentSwitcher).current == "results"
        await run(pilot, "sw")  # unique prefix
        assert app.query_one(ContentSwitcher).current == "sweep"

        await run(pilot, "q")
        await pilot.pause(0.5)
    assert not app.is_running


@async_test
async def test_unknown_and_page_specific_commands_report_errors_not_crashes():
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "frobnicate")
        assert any("unknown command :frobnicate" in m for m in notifications(app))
        await run(pilot, "width 40")  # a Results command, but we are on Connect
        assert any("unknown command :width" in m for m in notifications(app))
        assert app.is_running


@async_test
async def test_connect_and_disconnect_commands_drive_the_lab(monkeypatch: pytest.MonkeyPatch):
    handle = FakeHandle()
    app = connect_app(monkeypatch, handle)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "connect fake")
        await wait_until(pilot, lambda: "fake" in app.lab.handles)
        assert handle.connected
        table = app.query_one(DataTable)
        await wait_until(pilot, lambda: "connected" in str(table.get_row("fake")[1]))

        await run(pilot, "status")
        assert any("fake: connected" in m for m in notifications(app))

        await run(pilot, "disconnect fake")
        await wait_until(pilot, lambda: "fake" not in app.lab.handles)
        assert handle.disconnected


@async_test
async def test_results_commands_exist_only_on_the_results_page():
    app = app_mod.IyzeeApp(prefs_file=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "results")
        await run(pilot, "width 42")  # snaps to the nearest step
        run_list = app.query_one("#results-list")
        assert run_list.has_class("w-40")
        await run(pilot, "width soon")
        assert any("usage: :width" in m for m in notifications(app))


@async_test
async def test_history_and_tab_completion():
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "log")
        await pilot.press(":")
        await pilot.press("up")
        field = app.query_one("#command-input", Input)
        assert field.value == "log"

        field.value = ""
        await pilot.press(*"conn", "tab")
        assert field.value == "connect "
        await pilot.press("escape")


@async_test
async def test_scope_commands_report_why_they_cannot_run_without_a_scope():
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await run(pilot, "scope")
        await run(pilot, "acquire")
        assert any("cannot acquire waveforms" in m for m in notifications(app))
        await run(pilot, "tdiv fast")
        assert any("not a number" in m for m in notifications(app))
