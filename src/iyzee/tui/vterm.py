"""A virtual terminal screen: feed it VT100 text, get styled lines back.

This is the display half of "run a real IPython inside the console page".
IPython's terminal UI (prompt_toolkit) draws by writing VT100/xterm escape
sequences — cursor movement, erase, colours — to a terminal. There is no
terminal here (Textual owns the real one), so those sequences are fed to a
:class:`pyte.Screen`, which interprets them into a grid of cells, and the
Textual widget in ``terminal_view.py`` paints that grid.

Kept free of any Textual import so it can be tested on its own: give it text,
inspect the grid.

Scrollback
----------
prompt_toolkit's prompt is *inline* (not a full-screen app): cell output
scrolls the screen upward like a normal shell. Anything that scrolls off the
top would be gone without scrollback, which is unusable for a REPL (a long
array, a traceback), so this keeps pyte's ``HistoryScreen`` history and lets
the view be scrolled back through it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pyte
from rich.segment import Segment
from rich.style import Style

# pyte names ANSI colours the old VT100 way ("brown" is yellow) and the
# bright ones as one word ("brightred"); Rich wants "yellow" / "bright_red".
_NAMED = {"brown": "yellow"}

_StyleKey = tuple[str, str, bool, bool, bool, bool, bool, bool]


def _color(name: str) -> str | None:
    if name == "default":
        return None
    name = _NAMED.get(name, name)
    if name.startswith("bright") and not name.startswith("bright_"):
        return "bright_" + name[len("bright") :]
    if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
        return "#" + name  # 256-colour and truecolour arrive as bare hex
    return name


class VTermScreen:
    """A terminal grid with scrollback, driven by VT100 text."""

    def __init__(self, cols: int = 80, rows: int = 24, scrollback: int = 5000) -> None:
        self._screen = pyte.HistoryScreen(cols, rows, history=scrollback)
        # LNM: treat a bare "\n" as CR+LF, as a real terminal's line
        # discipline does for output (onlcr). prompt_toolkit and print()
        # both emit plain "\n".
        self._screen.set_mode(pyte.modes.LNM)
        self._stream = pyte.Stream(self._screen)
        self._styles: dict[_StyleKey, Style] = {}

    # -- input ----------------------------------------------------------

    def feed(self, data: str) -> int:
        """Interpret ``data``; returns how many lines scrolled into scrollback."""
        before = len(self._screen.history.top)
        self._stream.feed(data)
        return max(0, len(self._screen.history.top) - before)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(1, cols), max(1, rows)
        if (cols, rows) != (self.cols, self.rows):
            self._screen.resize(rows, cols)

    # -- geometry / state ----------------------------------------------------

    @property
    def cols(self) -> int:
        return int(self._screen.columns)

    @property
    def rows(self) -> int:
        return int(self._screen.lines)

    @property
    def scrollback_lines(self) -> int:
        return len(self._screen.history.top)

    @property
    def cursor(self) -> tuple[int, int]:
        """(column, row) on the live screen."""
        return self._screen.cursor.x, self._screen.cursor.y

    @property
    def cursor_hidden(self) -> bool:
        return bool(self._screen.cursor.hidden)

    # -- reading ----------------------------------------------------------------

    def _line(self, index: int) -> pyte.screens.StaticDefaultDict:
        """Line ``index`` of scrollback + screen (0 = oldest scrollback line)."""
        history = self._screen.history.top
        if index < len(history):
            return history[index]
        return self._screen.buffer[index - len(history)]

    def total_lines(self) -> int:
        return self.scrollback_lines + self.rows

    def text_lines(self, offset: int = 0) -> list[str]:
        """The visible window as plain text, ``offset`` lines above the bottom."""
        first = self._first_line(offset)
        return ["".join(self._cells(self._line(first + y))) for y in range(self.rows)]

    def _first_line(self, offset: int) -> int:
        offset = max(0, min(offset, self.scrollback_lines))
        return self.scrollback_lines - offset

    def _cells(self, line: pyte.screens.StaticDefaultDict) -> Iterator[str]:
        for x in range(self.cols):
            yield line[x].data

    def row_segments(
        self, y: int, offset: int = 0, cursor: bool = False, cursor_style: Style | None = None
    ) -> list[Segment]:
        """Styled segments for visible row ``y`` (0 = top of the window).

        ``cursor`` draws the terminal cursor when this row shows it. It is
        only drawn while looking at the live screen (``offset == 0``); once
        scrolled back the cursor is off-screen and drawing it would mislead.
        """
        line = self._line(self._first_line(offset) + y)
        show = cursor and offset == 0 and not self.cursor_hidden and y == self.cursor[1]
        cursor_x = self.cursor[0] if show else -1

        segments: list[Segment] = []
        run: list[str] = []
        run_style: Style | None = None
        for x in range(self.cols):
            char = line[x]
            if char.data == "":  # right half of a double-width character
                continue
            style = self._style_for(char)
            if x == cursor_x:
                style = style + (cursor_style or Style(reverse=True))
            if style != run_style and run:
                segments.append(Segment("".join(run), run_style))
                run = []
            run_style = style
            run.append(char.data)
        if run:
            segments.append(Segment("".join(run), run_style))
        return segments

    def _style_for(self, char: pyte.screens.Char) -> Style:
        key: _StyleKey = (
            char.fg,
            char.bg,
            char.bold,
            char.italics,
            char.underscore,
            char.strikethrough,
            char.reverse,
            char.blink,
        )
        style = self._styles.get(key)
        if style is None:
            style = Style(
                color=_color(char.fg),
                bgcolor=_color(char.bg),
                bold=char.bold or None,
                italic=char.italics or None,
                underline=char.underscore or None,
                strike=char.strikethrough or None,
                reverse=char.reverse or None,
                blink=char.blink or None,
            )
            self._styles[key] = style
        return style
