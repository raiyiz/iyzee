import asyncio

from textual.widgets import ContentSwitcher

from iyzee.tui.app import IyzeeApp, NavRail
from iyzee.tui.screens.connect import ConnectScreen
from iyzee.tui.screens.console import ConsoleScreen, IyzeeConsole
from iyzee.tui.screens.sweep import SweepScreen
from iyzee.tui.screens.traces import TracesScreen


def test_page_navigation_uses_a_shared_content_switcher() -> None:
    """Pages used to be independent Screens swapped via App.MODES; they're
    now widgets held inside one ContentSwitcher behind a persistent
    NavRail, so "isinstance(app.screen, XScreen)" no longer means
    anything — every page is mounted the whole time. Check which page is
    current instead, and that the rail's active-item highlight tracks it.
    """

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            switcher = app.screen.query_one(ContentSwitcher)
            nav = app.screen.query_one(NavRail)
            assert switcher.current == "connect"
            assert isinstance(switcher.get_child_by_id("connect"), ConnectScreen)

            await pilot.press("s")
            assert switcher.current == "sweep"
            assert isinstance(switcher.get_child_by_id("sweep"), SweepScreen)
            assert nav.query_one("#nav-sweep").has_class("-active")
            assert not nav.query_one("#nav-connect").has_class("-active")

            await pilot.press("t")
            assert switcher.current == "traces"
            assert isinstance(switcher.get_child_by_id("traces"), TracesScreen)

            await pilot.press("i")
            assert switcher.current == "console"
            assert isinstance(switcher.get_child_by_id("console"), ConsoleScreen)
            assert app.screen.focused is not None
            assert app.screen.focused.id == "console-terminal"

            await pilot.press("f1")
            assert switcher.current == "connect"

            await pilot.press("f4")
            assert switcher.current == "console"
            assert app.screen.focused is not None
            assert app.screen.focused.id == "console-terminal"

    asyncio.run(scenario())


def test_console_history_file_defaults_to_none_and_is_passed_through(tmp_path) -> None:
    """`IyzeeApp()` with no arguments — every test in this codebase,
    including every other one in this file — must default to no
    persistent history file at all (`console_history_file=None`, which
    the session turns into `:memory:`). Getting this default wrong
    would silently reintroduce every InteractiveShell in the whole test
    suite writing to a real, shared, ever-growing SQLite file again — see
    `default_history_file`'s docstring in ipython.py for what that caused
    last time. Also checks the opt-in path actually reaches the console's
    shell: passing a real path through the app constructor should end up
    configured on the running IPythonSession's shell, not just stored and
    ignored.
    """

    async def default_is_none() -> None:
        app = IyzeeApp()
        assert app.console_history_file is None
        async with app.run_test() as pilot:
            await pilot.press("i")
            console = app.screen.query_one(IyzeeConsole)
            assert console.session.shell.history_manager.hist_file == ":memory:"

    async def real_path_reaches_the_shell() -> None:
        history_file = tmp_path / "console_history.sqlite"
        app = IyzeeApp(console_history_file=history_file)
        async with app.run_test() as pilot:
            await pilot.press("i")
            console = app.screen.query_one(IyzeeConsole)
            assert console.session.shell.history_manager.hist_file == str(history_file)

    asyncio.run(default_is_none())
    asyncio.run(real_path_reaches_the_shell())
