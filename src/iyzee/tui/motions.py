"""Vim motions (``j k gg G Ctrl+D Ctrl+U``, ``/`` ``n`` ``N``) for whatever widget has the keyboard.

The app binds these keys once; this module decides what they mean for the focused widget: move
the cursor in a table, list or dropdown, scroll a log or any scrollable widget, or report that the
widget has no use for them (for ``j``/``k`` the app then falls back to moving focus, as it always
did). No Textual app is needed to use it, only a widget.
"""

from __future__ import annotations

import re

from textual.widget import Widget
from textual.widgets import DataTable, Label, ListItem, ListView, OptionList, RichLog

MOTIONS = ("down", "up", "top", "bottom", "half_down", "half_up")


def move(widget: Widget | None, motion: str) -> bool:
    """Apply ``motion`` to ``widget``; True if the widget took it."""
    if motion not in MOTIONS:
        raise ValueError(f"unknown motion {motion!r}")
    if widget is None:
        return False
    if isinstance(widget, DataTable | ListView | OptionList):
        return _move_cursor(widget, motion)
    return _scroll(widget, motion)


def go_to(widget: Widget | None, line: int) -> bool:
    """``<N>G`` / ``<N>gg``: the cursor or scroll position to line ``N`` (1-based, clamped)."""
    line = max(1, line)
    if isinstance(widget, DataTable):
        if widget.row_count:
            widget.move_cursor(row=min(line, widget.row_count) - 1)
            return True
    elif isinstance(widget, ListView):
        if len(widget):
            widget.index = min(line, len(widget)) - 1
            return True
    elif isinstance(widget, OptionList):
        if widget.option_count:
            widget.highlighted = min(line, widget.option_count) - 1
            return True
    elif widget is not None and _can_scroll(widget):
        widget.scroll_to(y=line - 1, animate=False)
        return True
    return False


def _move_cursor(widget: DataTable | ListView | OptionList, motion: str) -> bool:
    if motion == "down":
        widget.action_cursor_down()
    elif motion == "up":
        widget.action_cursor_up()
    elif motion == "half_down":
        widget.action_page_down()
    elif motion == "half_up":
        widget.action_page_up()
    elif isinstance(widget, DataTable):
        if widget.row_count:
            widget.move_cursor(row=0 if motion == "top" else widget.row_count - 1)
    elif isinstance(widget, ListView):
        if len(widget):
            widget.index = 0 if motion == "top" else len(widget) - 1
    elif widget.option_count:
        widget.highlighted = 0 if motion == "top" else widget.option_count - 1
    return True


def _can_scroll(widget: Widget) -> bool:
    # ``max_scroll_y`` alone is not enough: a squeezed button reports a positive one.
    return widget.allow_vertical_scroll and widget.max_scroll_y > 0


def _scroll(widget: Widget, motion: str) -> bool:
    """Scroll ``widget``, or for the big jumps the nearest scrollable ancestor (a tall page)."""
    if motion in ("down", "up"):
        if not _can_scroll(widget):
            return False  # a button, a field: the caller moves focus instead
        (widget.scroll_down if motion == "down" else widget.scroll_up)(animate=False)
        return True
    candidates = (widget, *(a for a in widget.ancestors if isinstance(a, Widget)))
    target = next((w for w in candidates if _can_scroll(w)), None)
    if target is None:
        return False
    if motion == "top":
        target.scroll_home(animate=False)
    elif motion == "bottom":
        target.scroll_end(animate=False)
    else:
        half = max(1, target.scrollable_content_region.height // 2)
        target.scroll_relative(y=half if motion == "half_down" else -half, animate=False)
    return True


def texts(widget: Widget) -> list[str]:
    """The searchable text of each row or line of ``widget`` (empty if it has none)."""
    if isinstance(widget, DataTable):
        return [
            " ".join(str(cell) for cell in widget.get_row_at(i)) for i in range(widget.row_count)
        ]
    if isinstance(widget, ListView):
        return [
            " ".join(str(label.visual) for label in item.query(Label))
            for item in widget.children
            if isinstance(item, ListItem)
        ]
    if isinstance(widget, OptionList):
        return [str(widget.get_option_at_index(i).prompt) for i in range(widget.option_count)]
    if isinstance(widget, RichLog):
        return [line.text for line in widget.lines]
    return []


def _here(widget: Widget) -> int:
    if isinstance(widget, DataTable):
        return widget.cursor_row
    if isinstance(widget, ListView):
        return widget.index or 0
    if isinstance(widget, OptionList):
        return widget.highlighted or 0
    return int(widget.scroll_y)


def search(widget: Widget | None, pattern: str, step: int = 1) -> bool:
    """``/pattern`` (and ``n`` with ``step=1``, ``N`` with ``-1``): go to the next matching row.

    The pattern is a regular expression (a literal if it is not a valid one), case-insensitive
    unless it contains a capital, and the search wraps round. The row you are on is tried last.
    """
    rows = texts(widget) if widget is not None else []
    if widget is None or not rows or not pattern:
        return False
    flags = re.IGNORECASE if pattern == pattern.lower() else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        regex = re.compile(re.escape(pattern), flags)
    here = _here(widget)
    for offset in range(1, len(rows) + 1):
        index = (here + step * offset) % len(rows)
        if regex.search(rows[index]):
            return go_to(widget, index + 1)
    return False
