"""Integration check that a completed sweep's results actually reach the
IPython console's namespace — the wiring in ConsoleScreen._namespace(),
not just the LastRun dataclass or namespace_from_handles() in isolation.
"""

from __future__ import annotations

import asyncio

from iyzee.experiment import StepResult
from iyzee.tui.app import IyzeeApp
from iyzee.tui.workers import LastRun


def test_last_run_results_reach_console_namespace() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            result = StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})
            app.last_run = LastRun(kind="bandwidth", results=[result], path=None)

            await pilot.press("i")
            await pilot.pause()

            shell = app._console_screen.console.shell.shell
            assert shell.user_ns["results"] == [result]
            assert shell.user_ns["last_run"].kind == "bandwidth"

    asyncio.run(scenario())


def test_no_last_run_gives_empty_results_not_a_crash() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()

            shell = app._console_screen.console.shell.shell
            assert shell.user_ns["results"] == []
            assert shell.user_ns["last_run"] is None

    asyncio.run(scenario())
