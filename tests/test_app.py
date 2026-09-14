import asyncio

from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.connect import ConnectScreen
from iyzee.tui.screens.console import ConsoleScreen
from iyzee.tui.screens.sweep import SweepScreen
from iyzee.tui.screens.traces import TracesScreen


def test_screen_navigation_uses_textual_modes() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            assert isinstance(app.screen, ConnectScreen)

            await pilot.press("s")
            assert isinstance(app.screen, SweepScreen)

            await pilot.press("t")
            assert isinstance(app.screen, TracesScreen)

            await pilot.press("i")
            assert isinstance(app.screen, ConsoleScreen)
            assert app.screen.focused is not None
            assert app.screen.focused.id == "console-input"

            await pilot.press("f1")
            assert isinstance(app.screen, ConnectScreen)

            await pilot.press("f4")
            assert isinstance(app.screen, ConsoleScreen)
            assert app.screen.focused is not None
            assert app.screen.focused.id == "console-input"

    asyncio.run(scenario())
