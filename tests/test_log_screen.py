from __future__ import annotations

import logging

from helpers import async_test, wait_until
from textual.widgets import RichLog, Select

from iyzee.tui.app import IyzeeApp


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
