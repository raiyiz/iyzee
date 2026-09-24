from __future__ import annotations

import logging

import pytest
from helpers import async_test, wait_until
from textual.widgets import RichLog, Select

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.log import LogScreen


@async_test
async def test_log_screen_shows_buffered_and_live_records() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        logging.getLogger("iyzee.tui").warning("a warning before opening the log page")
        await pilot.press("l")
        await pilot.pause(0.3)

        screen = app.query_one(LogScreen)
        view = screen.query_one("#log-view", RichLog)
        assert any("a warning before opening the log page" in line.text for line in view.lines)

        before = len(view.lines)
        logging.getLogger("iyzee.tui").info("an info event while on the log page")
        await wait_until(pilot, lambda: len(view.lines) > before)


@async_test
async def test_log_screen_level_filter_hides_lower_severity_records() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        log = logging.getLogger("iyzee.tui")
        log.info("an info-level record")
        await pilot.press("l")
        await pilot.pause(0.3)

        screen = app.query_one(LogScreen)
        view = screen.query_one("#log-view", RichLog)
        assert any("an info-level record" in line.text for line in view.lines)

        screen.query_one("#log-level", Select).value = str(logging.WARNING)
        await pilot.pause(0.3)
        assert not any("an info-level record" in line.text for line in view.lines)


@async_test
async def test_log_screen_exception_records_include_a_traceback() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        log = logging.getLogger("iyzee.tui")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("something broke")
        await pilot.press("l")
        await pilot.pause(0.3)

        screen = app.query_one(LogScreen)
        view = screen.query_one("#log-view", RichLog)
        text = "\n".join(line.text for line in view.lines)
        assert "something broke" in text
        assert "ValueError: boom" in text


@async_test
async def test_log_screen_can_browse_a_rotated_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from iyzee.tui import logging_support

    log_root = tmp_path / "logs"
    log_root.mkdir()
    (log_root / "iyzee.log.1").write_text("2026-01-01 00:00:00 WARNING iyzee.tui: an old record\n")
    monkeypatch.setattr(logging_support, "LOG_ROOT", log_root)

    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.pause(0.3)

        screen = app.query_one(LogScreen)
        source = screen.query_one("#log-source", Select)
        source.value = str(log_root / "iyzee.log.1")
        await pilot.pause(0.3)

        view = screen.query_one("#log-view", RichLog)
        assert any("an old record" in line.text for line in view.lines)
