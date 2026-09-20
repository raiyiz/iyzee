"""Integration check that a completed sweep's results actually reach the
IPython console through `lab` — exercising the real ConsoleScreen/
IPythonSession wiring end-to-end, not just LabProxy in isolation.
"""

from __future__ import annotations

import asyncio

from iyzee.experiment import StepResult
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import ConsoleScreen
from iyzee.tui.workers import LastRun


def test_last_run_reaches_lab_in_the_running_app() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            result = StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})
            app.last_run = LastRun(kind="bandwidth", results=[result], path=None)

            await pilot.press("i")
            await pilot.pause()

            console_page = app.query_one(ConsoleScreen)
            lab = console_page.console.session.shell.user_ns["lab"]
            assert lab.results == [result]
            assert lab.last_run.kind == "bandwidth"

    asyncio.run(scenario())


def test_no_last_run_gives_empty_results_not_a_crash() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()

            console_page = app.query_one(ConsoleScreen)
            lab = console_page.console.session.shell.user_ns["lab"]
            assert lab.results == []
            assert lab.last_run is None

    asyncio.run(scenario())


def test_disconnecting_mxa_is_immediately_reflected_in_lab_no_refresh_needed() -> None:
    """The scenario that used to require an explicit stale-name cleanup
    pass — here there's nothing to clean up because lab.mx is never
    cached in the first place."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            await pilot.pause()
            console_page = app.query_one(ConsoleScreen)
            lab = console_page.console.session.shell.user_ns["lab"]

            class FakeHandle:
                device = object()

                def connect(self) -> None:
                    raise NotImplementedError

                def disconnect(self) -> None:
                    raise NotImplementedError

                def probe(self) -> str:
                    raise NotImplementedError

            app.handles["mxa"] = FakeHandle()
            assert repr(lab.mx) == repr(FakeHandle.device)

            del app.handles["mxa"]
            try:
                lab.mx
            except AttributeError as exc:
                assert "not connected" in str(exc)
            else:
                raise AssertionError("expected AttributeError")

    asyncio.run(scenario())
