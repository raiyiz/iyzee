import asyncio

from textual.widgets import Input, RichLog, TextArea
from textual_plotext import PlotextPlot

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


def test_console_status_line_shows_vim_mode() -> None:
    """The console's status line is the only UI feedback for which vim
    mode is active (INSERT vs NORMAL) — regression test for that
    indicator actually updating on Escape/i."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            status = app.screen.query_one("#console-status")
            assert "-- INSERT --" in str(status.render())

            await pilot.press("escape")
            assert "-- NORMAL --" in str(app.screen.query_one("#console-status").render())

            await pilot.press("i")
            assert "-- INSERT --" in str(app.screen.query_one("#console-status").render())

    asyncio.run(scenario())


def test_console_display_renders_rich_html_output() -> None:
    """display() with a text/html-only object used to fall back to a bare
    `<object at 0x...>` repr (base DisplayPublisher only handles
    text/plain). Regression test that html gets a real (tag-stripped)
    rendering instead."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(TextArea)
            text_area.load_text(
                "from IPython.display import display\n"
                "class Foo:\n"
                "    def _repr_html_(self):\n"
                "        return '<b>hello</b> world'\n"
                "display(Foo())"
            )
            await pilot.press("shift+enter")
            await pilot.pause(0.3)
            log = app.screen.query_one(RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "hello world" in rendered
            assert "object at 0x" not in rendered

    asyncio.run(scenario())


def test_console_display_placeholders_image_output() -> None:
    """Image mimetypes (matplotlib figures, ...) can't be rendered inline
    in this console — regression test that they get a visible placeholder
    instead of silently vanishing."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(TextArea)
            text_area.load_text(
                "from IPython.display import display\n"
                "display({'text/plain': 'a figure', 'image/png': b'fake'}, raw=True)"
            )
            await pilot.press("shift+enter")
            await pilot.pause(0.3)
            log = app.screen.query_one(RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "image/png" in rendered
            assert "not supported" in rendered

    asyncio.run(scenario())


def test_escape_leaves_input_field_for_navigation() -> None:
    """Plain Input fields (RBW, IP addresses, ...) have no vim mode of
    their own and, before this, no Escape binding either -- once focused,
    j/k/c/s/t/i all typed as literal characters instead of navigating or
    switching screens, with no way out except Tab/click. Regression test
    that Escape blurs the field and hands control back to the app's own
    bindings (j/k focus stepping, and the global screen-switch keys)."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("s")
            field = app.screen.query(Input).first()
            field.focus()

            await pilot.press("j")
            assert field.value == "j", "j should still type into a focused Input"

            await pilot.press("escape")
            assert app.screen.focused is not field

            await pilot.press("i")
            assert isinstance(app.screen, ConsoleScreen), (
                "global screen-switch keys should work again once no Input has focus"
            )

    asyncio.run(scenario())


def test_console_plots_matplotlib_figures_in_place() -> None:
    """A bare matplotlib Figure used to degrade to the unhelpful
    `<Figure size ... with N Axes>` text repr with no way to actually see
    it. Regression test that its line data now renders into the console's
    plot panel (reusing textual_plotext, already a project dependency and
    already used the same way by the sweep screen)."""

    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test() as pilot:
            await pilot.press("i")
            text_area = app.screen.query_one(TextArea)
            text_area.load_text(
                "import matplotlib\n"
                "matplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "fig, ax = plt.subplots()\n"
                "ax.plot([1, 2, 3], [4, 5, 6], label='trace')\n"
                "fig"
            )
            await pilot.press("shift+enter")
            await pilot.pause(0.3)

            plot = app.screen.query_one("#console-plot", PlotextPlot)
            assert plot.display is True

            log = app.screen.query_one(RichLog)
            rendered = " ".join(str(seg) for line in log.lines for seg in line)
            assert "plotted 1 line" in rendered
            assert "Figure size" not in rendered

    asyncio.run(scenario())
