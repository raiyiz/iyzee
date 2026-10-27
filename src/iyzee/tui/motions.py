"""Vim motions (``j k gg G Ctrl+D Ctrl+U``) for whatever widget has the keyboard.

The app binds these keys once; this module decides what they mean for the focused widget: move
the cursor in a table, list or dropdown, scroll a log or any scrollable widget, or report that the
widget has no use for them (for ``j``/``k`` the app then falls back to moving focus, as it always
did). No Textual app is needed to use it, only a widget.
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import DataTable, ListView, OptionList

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
