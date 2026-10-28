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
async def test_the_command_line_opens_navigates_completes_and_reports_errors_not_crashes():
    app = app_mod.IyzeeApp(prefs_file=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        page = lambda: app.query_one(ContentSwitcher).current  # noqa: E731
        before = app.focused
        await pilot.press(":")
        bar = app.query_one(CommandBar)
        assert bar.is_open and isinstance(app.focused, CommandInput)
        await pilot.press("escape")
        assert not bar.is_open and app.focused is before  # closing returns the focus

        await run(pilot, "frobnicate")
        assert any("unknown command :frobnicate" in m for m in notifications(app))
        await run(pilot, "width 40")  # a Results command, but we are on Connect
        assert any("unknown command :width" in m for m in notifications(app))
        await run(pilot, "scope")  # scope commands say why they cannot run without a scope
        await run(pilot, "acquire")
        assert any("cannot acquire waveforms" in m for m in notifications(app))
        await run(pilot, "tdiv fast")
        assert any("not a number" in m for m in notifications(app))

        await run(pilot, "results")  # a page name switches pages; its own commands now exist
        assert page() == "results"
        await run(pilot, "width 42")  # snaps to the nearest step
        assert app.query_one("#results-list").has_class("w-40")
        await run(pilot, "width soon")
        assert any("usage: :width" in m for m in notifications(app))
        await run(pilot, "sw")  # a unique prefix
        assert page() == "sweep"

        await run(pilot, "log")  # history recalls it; Tab completes a command word
        await pilot.press(":", "up")
        field = app.query_one("#command-input", Input)
        assert field.value == "log"
        field.value = ""
        await pilot.press(*"conn", "tab")
        assert field.value == "connect "
        await pilot.press("escape")

        await run(pilot, "q")  # asks first, like the q key...
        assert app.is_running
        await run(pilot, "qq")  # ...and :qq quits at once
        await wait_until(pilot, lambda: not app.is_running)


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
