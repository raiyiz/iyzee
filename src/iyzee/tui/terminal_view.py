"""A Textual widget that shows a virtual terminal and types into it.

Together with ``vterm.py`` (the screen model) and ``termkeys.py`` (keystroke
encoding), this is what lets the console page host IPython's own terminal UI.
The widget knows nothing about IPython: it is handed a *backend* to send
keystrokes to, and is fed the backend's output.
"""

from __future__ import annotations

from typing import Protocol

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from .termkeys import key_to_bytes
from .vterm import VTermScreen

# Keys the *app* owns, so they are never sent to the terminal: page switching
# (F1-F4), quit, and the command palette. Everything else is the terminal's.
# That includes Tab, Escape and Ctrl+P/N/R — IPython's completion, vi mode and
# history — which is why leaving the terminal is done with the F-keys.
_APP_KEYS = frozenset({"f1", "f2", "f3", "f4", "ctrl+q", "ctrl+backslash"})

# Lines moved per mouse-wheel notch, and per Shift+PageUp/PageDown press
# (a page being the visible height).
_WHEEL_LINES = 3


class TerminalBackend(Protocol):
    """What the view talks to: something that consumes keystrokes."""

    def send(self, data: bytes) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def interrupt(self) -> None: ...


class TerminalView(Widget, can_focus=True):
    """Displays a :class:`VTermScreen` and forwards keystrokes to a backend."""

    DEFAULT_CSS = """
    TerminalView {
        height: 1fr;
        min-height: 6;
        border: round $primary-darken-1;
        background: $surface;
        color: $text;
    }
    TerminalView:focus {
        border: round $accent;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002 - Textual's own name
        super().__init__(id=id)
        self.vterm = VTermScreen(80, 24)
        self._backend: TerminalBackend | None = None
        self._offset = 0  # lines scrolled back from the live screen

    # -- wiring ---------------------------------------------------------------------

    def attach(self, backend: TerminalBackend) -> None:
        self._backend = backend
        backend.resize(self.vterm.rows, self.vterm.cols)

    def feed(self, text: str) -> None:
        """Show terminal output. Called on the UI thread."""
        added = self.vterm.feed(text)
        if self._offset > 0:
            # Looking at older output: keep it where it is as new lines scroll in.
            self._offset = min(self._offset + added, self.vterm.scrollback_lines)
        self.refresh()

    def text_lines(self) -> list[str]:
        """The visible window as plain text (for tests and debugging)."""
        return [line.rstrip() for line in self.vterm.text_lines(self._offset)]

    @property
    def scrolled_back(self) -> int:
        return self._offset

    # -- geometry ---------------------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        cols, rows = self.size.width, self.size.height
        if cols < 1 or rows < 1:
            return  # hidden (another page is showing): keep the last real size
        self.vterm.resize(cols, rows)
        self._offset = min(self._offset, self.vterm.scrollback_lines)
        if self._backend is not None:
            self._backend.resize(rows, cols)
        self.refresh()

    # -- painting -----------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        if y >= self.vterm.rows:
            return Strip.blank(self.size.width)
        cursor_style = Style(reverse=True) if self.has_focus else Style(underline=True)
        segments = self.vterm.row_segments(
            y, offset=self._offset, cursor=True, cursor_style=cursor_style
        )
        strip = Strip(segments, self.vterm.cols)
        width = self.size.width
        if y == 0 and self._offset > 0:
            strip = self._with_scrollback_marker(strip, width)
        return strip.adjust_cell_length(width)

    def _with_scrollback_marker(self, strip: Strip, width: int) -> Strip:
        label = f" ↑ {self._offset} lines back — End or type to return "
        if width <= len(label):
            return strip
        marker = Segment(label, Style(reverse=True, bold=True))
        return Strip([*strip.crop(0, width - len(label)), marker], width)

    def on_focus(self) -> None:
        self.refresh()  # the cursor is drawn differently when focused

    def on_blur(self) -> None:
        self.refresh()

    # -- keyboard -------------------------------------------------------------------------

    def check_consume_key(self, key: str, character: str | None) -> bool:
        """Claim every key except the app's own, so the Footer does not offer
        (and Textual does not run) bindings like ``c``/``s``/``t`` that would
        otherwise fire while you are typing into IPython."""
        return key not in _APP_KEYS

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key in _APP_KEYS:
            return  # a real app binding; let it through

        event.stop()
        event.prevent_default()

        if key == "shift+pageup":
            self._scroll(self.vterm.rows)
            return
        if key == "shift+pagedown":
            self._scroll(-self.vterm.rows)
            return
        if key == "ctrl+c":
            if self._backend is not None:
                self._backend.interrupt()
            self._offset = 0
            self.refresh()
            return

        data = key_to_bytes(key, event.character if event.is_printable else None)
        if data is None or self._backend is None:
            return  # no terminal encoding for this key: swallowed on purpose
        if self._offset:
            self._offset = 0  # typing returns to the live screen
            self.refresh()
        self._backend.send(data)

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        if self._backend is None or not event.text:
            return
        # Bracketed paste: prompt_toolkit inserts it as one edit (newlines and
        # all) instead of running each line as if it had been typed.
        self._backend.send(b"\x1b[200~" + event.text.encode("utf-8") + b"\x1b[201~")
        self._offset = 0
        self.refresh()

    # -- scrollback -----------------------------------------------------------------------

    def _scroll(self, lines: int) -> None:
        """Positive = towards older output."""
        self._offset = max(0, min(self._offset + lines, self.vterm.scrollback_lines))
        self.refresh()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self._scroll(_WHEEL_LINES)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self._scroll(-_WHEEL_LINES)
