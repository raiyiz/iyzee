from __future__ import annotations

import logging

import pytest
from helpers import async_test, wait_until
from textual.widgets import RichLog, Select

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.log import LogScreen


@async_test
async def test_log_screen_shows_buffered_live_filtered_and_exception_records() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        log = logging.getLogger("iyzee.tui")

        log.info("an info-level record")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("something broke")

        await pilot.press("l")
        view = app.query_one("#log-view", RichLog)
        await wait_until(pilot, lambda: len(view.lines) >= 3)

        text = "\n".join(line.text for line in view.lines)
        assert "an info-level record" in text
        assert "something broke" in text
        assert "ValueError: boom" in text

        before = len(view.lines)
        log.warning("a warning while on the log page")
        await wait_until(pilot, lambda: len(view.lines) > before)

        app.query_one("#log-level", Select).value = str(logging.WARNING)
        await wait_until(
            pilot,
            lambda: not any("an info-level record" in line.text for line in view.lines),
        )
        assert any("a warning while on the log page" in line.text for line in view.lines)


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
