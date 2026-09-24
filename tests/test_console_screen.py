"""Integration check that the running app exposes live state through the IPython console."""

from __future__ import annotations

from helpers import async_test

from iyzee.experiment import StepResult
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.console import ConsoleScreen
from iyzee.tui.workers import LastRun


@async_test
async def test_console_lab_reflects_running_app_state() -> None:
    """Prove the real ConsoleScreen sees live application state.

    LabProxy's individual properties are covered by the unit tests in
    test_ipython.py. This integration test only needs to prove that the real
    Textual console wires the proxy to the running app and sees updates
    without a refresh pass.
    """
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("i")
        await pilot.pause()

        console_page = app.query_one(ConsoleScreen)
        lab = console_page.console.session.shell.user_ns["lab"]

        assert lab.results == []
        assert lab.last_run is None

        result = StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})
        app.last_run = LastRun(kind="bandwidth", results=[result], path=None)
        assert lab.results == [result]
        assert lab.last_run.kind == "bandwidth"

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
