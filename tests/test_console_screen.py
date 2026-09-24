"""Integration check for the real ConsoleScreen/IPythonSession wiring.

The LabProxy unit tests cover individual properties; this page-level test
protects the connection between the live Textual app and the terminal shell.
"""

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

        # The initial empty state is significant: the proxy must consult the
        # live app rather than assuming a sweep has already happened.
        assert lab.results == []
        assert lab.last_run is None

        result = StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})
        # No refresh is required: LabProxy resolves results directly from the app.
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

        # The device lookup must also be live; disconnecting removes the attribute
        # without any stale-name cleanup or explicit console refresh.
        del app.handles["mxa"]
        try:
            lab.mx
        except AttributeError as exc:
            assert "not connected" in str(exc)
        else:
            raise AssertionError("expected AttributeError")
