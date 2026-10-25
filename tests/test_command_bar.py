from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FakeHandle, async_test, connect_app, notifications, wait_until
from textual.widgets import ContentSwitcher, Input

from iyzee.tui.command_bar import CommandBar, CommandInput
from iyzee.tui.screens import results as results_mod


@pytest.fixture(autouse=True)
def quiet_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(results_mod, "_DATA_ROOT", tmp_path)  # never read the real data dir


async def run(pilot, line: str) -> None:
    await pilot.press(":")
    await pilot.press(*line)
    await pilot.press("enter")
    await pilot.pause(0)


@async_test
async def test_command_bar_focus_history_and_completion(app):
    async with app.run_test() as pilot:
        before = app.focused
        await pilot.press(":")
        bar = app.query_one(CommandBar)
        assert bar.is_open and isinstance(app.focused, CommandInput)

        await pilot.press("escape")
        assert not bar.is_open and app.focused is before

        await run(pilot, "log")
        await pilot.press(":")
        await pilot.press("up")
        field = app.query_one("#command-input", Input)
        assert field.value == "log"

        field.value = ""
        await pilot.press(*"conn", "tab")
        assert field.value == "connect "
        await pilot.press("escape")
        assert not bar.is_open


@async_test
async def test_command_operations_validation_navigation_and_quit(
    monkeypatch: pytest.MonkeyPatch,
):
    # Keyboard submission/history are covered above; exercise the same dispatcher
    # directly here so this test measures command behavior, not per-key UI overhead.
    handle = FakeHandle()
    app = connect_app(monkeypatch, handle)
    async with app.run_test() as pilot:
        app.run_command("connect fake")
        await wait_until(pilot, lambda: "fake" in app.lab.handles)
        assert handle.connected

        app.run_command("status")
        await wait_until(pilot, lambda: any("fake: connected" in m for m in notifications(app)))

        app.run_command("disconnect fake")
        await wait_until(pilot, lambda: "fake" not in app.lab.handles)
        assert handle.disconnected

        app.run_command("frobnicate")
        await wait_until(
            pilot, lambda: any("unknown command :frobnicate" in m for m in notifications(app))
        )
        assert app.is_running

        # Results-only commands must be rejected elsewhere, then validated on Results.
        app.run_command("width 40")
        await wait_until(
            pilot, lambda: any("unknown command :width" in m for m in notifications(app))
        )
        switcher = app.query_one(ContentSwitcher)
        app.run_command("scope")
        await wait_until(pilot, lambda: switcher.current == "scope")
        app.run_command("acquire")
        await wait_until(
            pilot, lambda: any("cannot acquire waveforms" in m for m in notifications(app))
        )

        app.run_command("results")
        await wait_until(pilot, lambda: switcher.current == "results")
        app.run_command("width 42")  # snaps to the nearest step
        run_list = app.query_one("#results-list")
        await wait_until(pilot, lambda: run_list.has_class("w-40"))
        app.run_command("width soon")
        await wait_until(pilot, lambda: any("usage: :width" in m for m in notifications(app)))

        app.run_command("sw")  # unique prefix
        await wait_until(pilot, lambda: switcher.current == "sweep")
        app.run_command("q")
        await wait_until(pilot, lambda: not app.is_running)
