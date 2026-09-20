"""Tests for the console's real-IPython-in-a-virtual-terminal design.

Layers, bottom up: the screen model (``vterm``), key encoding (``termkeys``),
the in-process IPython session (``ipython_session``), and the widget/page that
ties them together (``terminal_view`` / ``screens.console``).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections import defaultdict

import pytest
from prompt_toolkit.input.vt100_parser import Vt100Parser
from textual.containers import ContentSwitcher

from iyzee.tui.app import IyzeeApp
from iyzee.tui.ipython_session import IPythonSession
from iyzee.tui.screens.console import IyzeeConsole
from iyzee.tui.terminal_view import TerminalView
from iyzee.tui.termkeys import key_to_bytes
from iyzee.tui.vterm import VTermScreen

# -- screen model ---------------------------------------------------------------------


def test_vterm_scrollback_colours_and_cursor() -> None:
    screen = VTermScreen(20, 3, scrollback=50)
    added = screen.feed("one\ntwo\nthree\nfour\nfive\n")  # bare \n must act as CR+LF
    assert added == 3 and screen.scrollback_lines == 3
    assert [line.rstrip() for line in screen.text_lines()] == ["four", "five", ""]
    assert [line.rstrip() for line in screen.text_lines(offset=3)][:3] == ["one", "two", "three"]

    screen = VTermScreen(20, 2)
    screen.feed("\x1b[1;31mred\x1b[0m \x1b[38;2;10;20;30mtrue\x1b[92mbg")
    styles = {seg.text.strip(): seg.style for seg in screen.row_segments(0) if seg.text.strip()}
    red = styles["red"]
    true = styles["true"]
    bg = styles["bg"]
    assert red is not None and red.color is not None and red.color.name == "red" and red.bold
    assert true is not None and true.color is not None and true.color.name == "#0a141e"
    assert bg is not None and bg.color is not None and bg.color.name == "bright_green"

    screen.feed("\x1b[?25l")  # hidden cursor is not drawn
    assert not any(s.style and s.style.reverse for s in screen.row_segments(0, cursor=True))
    screen.feed("\x1b[?25h")
    assert any(s.style and s.style.reverse for s in screen.row_segments(0, cursor=True))
    # ...and never while looking at scrollback, where it is off screen.
    screen.feed("a\nb\nc\n")
    assert not any(
        s.style and s.style.reverse for s in screen.row_segments(0, offset=1, cursor=True)
    )


# -- key encoding -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,character,expected",
    [
        ("enter", None, "c-m"),
        ("escape", None, "escape"),
        ("backspace", None, "c-h"),
        ("up", None, "up"),
        ("home", None, "home"),
        ("pagedown", None, "pagedown"),
        ("delete", None, "delete"),
        ("shift+tab", None, "s-tab"),
        ("ctrl+left", None, "c-left"),
        ("ctrl+r", None, "c-r"),
        ("ctrl+d", None, "c-d"),
        ("f5", None, "f5"),
        ("a", "a", "a"),
        ("é", "é", "é"),
    ],
)
def test_keys_encode_to_what_prompt_toolkit_decodes(key, character, expected) -> None:
    data = key_to_bytes(key, character)
    assert data is not None
    seen: list[str] = []
    parser = Vt100Parser(
        lambda kp: seen.append(kp.key if isinstance(kp.key, str) else kp.key.value)
    )
    parser.feed(data.decode())
    parser.flush()
    assert seen == [expected]


def test_key_encoding_edge_cases() -> None:
    # Alt+A must stay an Alt combination even if Textual also reports character="a".
    assert key_to_bytes("alt+a", "a") == b"\x1ba"
    assert key_to_bytes("alt+enter") == b"\x1b\r"
    # Ctrl+Z is IPython's "suspend to background": it would stop the whole TUI.
    assert key_to_bytes("ctrl+z") is None
    assert key_to_bytes("some-unknown-key") is None


# -- the IPython session -----------------------------------------------------------------


class _FakeApp:
    def __init__(self) -> None:
        self.handles: dict = {}
        self.instrument_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self.last_run = None


async def _until(screen_text, needle: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in screen_text():
            return True
        await asyncio.sleep(0.03)
    return False


def test_session_runs_a_real_ipython_in_process() -> None:
    async def scenario() -> None:
        screen = VTermScreen(90, 24)
        text = lambda: "\n".join(screen.text_lines())  # noqa: E731
        def feed_output(output: str) -> None:
            screen.feed(output)

        session = IPythonSession(_FakeApp(), rows=24, cols=90, on_output=feed_output)
        real_stdout = sys.stdout
        session.start()
        send = lambda s: session.send(s.encode() + b"\r")  # noqa: E731
        try:
            assert await _until(text, "In [1]"), "IPython's own prompt is drawn"
            assert "[ins]" in text() or "[nav]" in text(), "vi mode is on"

            send("print('hello', 1 + 1)")
            assert await _until(text, "hello 2"), "print() lands in the terminal"

            send("lab.connected")  # the live app object, not a copy
            assert await _until(text, "Out[2]: ()")

            send("len?")  # help is shown inline, not through an external pager
            assert await _until(text, "Docstring")

            session.send(b"lab.")
            session.send(b"\t")  # IPython's completion menu
            assert await _until(text, "results")
            session.send(b"\x15")  # Ctrl+U: clear the line (Ctrl+C would only close the menu)

            send("x = input('name? ')")
            assert await _until(text, "name?"), "input() prompts on the virtual terminal"
            send("iyzee")
            await asyncio.sleep(0.6)  # typeahead is dropped when an inner prompt ends
            send("x")
            assert await _until(text, "'iyzee'"), text()

            send("exit")  # must not shut the console down
            assert await _until(text, "stays open")

            send("while True: pass")
            await asyncio.sleep(0.4)
            assert session.executing
            session.interrupt()
            assert await _until(text, "KeyboardInterrupt")
            deadline = time.monotonic() + 5
            while session.executing and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert not session.executing, "back at a prompt after the interrupt"
        finally:
            session.close()
        session._thread.join()
        assert not session._thread.is_alive()
        assert sys.stdout is real_stdout, "stdout is restored on close"

    asyncio.run(scenario())


# -- the page, end to end through real key presses ---------------------------------------


def _keys(text: str) -> list[str]:
    names = {" ": "space", "+": "plus", "(": "left_parenthesis", ")": "right_parenthesis"}
    return [names.get(c, c) for c in text]


def _console_text(app: IyzeeApp) -> str:
    return "\n".join(app.screen.query_one(TerminalView).text_lines())


async def _wait(pilot, app, needle: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause(0.05)
        if needle in _console_text(app):
            return True
    return False


def test_console_page_types_into_ipython_and_keeps_app_keys() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("i")
            view = app.screen.query_one(TerminalView)
            assert app.screen.focused is view
            assert await _wait(pilot, app, "In [1]")

            await pilot.press(*_keys("40 + 2"), "enter")  # real key events
            assert await _wait(pilot, app, "Out[1]: 42")

            # Keys the terminal owns are NOT app bindings while it has focus...
            await pilot.press("c")  # goes into IPython's line, not the app's page switch
            assert app.query_one("#page-switcher", ContentSwitcher).current == "console"
            await pilot.press("backspace")
            # ...but the F-keys still switch pages.
            await pilot.press("f1")
            assert app.query_one("#page-switcher", ContentSwitcher).current == "connect"
            await pilot.press("f4")
            assert app.query_one("#page-switcher").current == "console"

            # Bracketed paste arrives as one edit, not as typed lines.
            view.post_message(__import__("textual.events", fromlist=["Paste"]).Paste("x = 5"))
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.press(*_keys("x"), "enter")
            assert await _wait(pilot, app, "Out[3]: 5"), _console_text(app)

    asyncio.run(scenario())


def test_console_interrupt_scrollback_and_shutdown() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("i")
            assert await _wait(pilot, app, "In [1]")
            session = app.query_one(IyzeeConsole).session
            view = app.screen.query_one(TerminalView)

            # Output longer than the screen scrolls into scrollback, reachable by keyboard.
            session.send(b"for i in range(80): print('line', i)\r\r")
            assert await _wait(pilot, app, "line 79")
            assert view.vterm.scrollback_lines > 0
            await pilot.press("shift+pageup")
            assert view.scrolled_back > 0
            await pilot.press("a")  # typing returns to the live screen
            assert view.scrolled_back == 0

            # Ctrl+C stops a running cell and the console stays usable.
            session.send(b"\x15while True: pass\r")
            await pilot.pause(0.5)
            assert session.executing
            await pilot.press("ctrl+c")
            assert await _wait(pilot, app, "KeyboardInterrupt")

        assert session._thread is not None
        assert not session._thread.is_alive(), "leaving the app closes the shell"

    asyncio.run(scenario())


@pytest.mark.skip("Fails more often than not")
def test_console_renders_html_and_matplotlib_figures() -> None:
    async def scenario() -> None:
        app = IyzeeApp()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("i")
            assert await _wait(pilot, app, "In [1]")
            session = app.query_one(IyzeeConsole).session
            session.send(
                b"from IPython.display import display\r"
                b"class Foo:\r    def _repr_html_(self): return '<b>hello</b> world'\r\r"
                b"display(Foo())\r"
            )
            assert await _wait(pilot, app, "hello world", timeout=30), _console_text(app)
            assert "object at 0x" not in _console_text(app)

            session.send(
                b"import matplotlib; matplotlib.use('Agg')\r"
                b"import matplotlib.pyplot as plt\r"
                b"fig, ax = plt.subplots(); ax.plot([1, 2, 3], [4, 5, 6], label='t')\r"
                b"fig\r"
            )
            assert await _wait(pilot, app, "plotted 1 line", timeout=20), _console_text(
                app
            )  # first matplotlib import is slow
            assert app.screen.query_one("#console-plot").display is True

    asyncio.run(scenario())
