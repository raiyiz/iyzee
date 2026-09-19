"""Helpers for showing text that came from outside the app.

Instrument drivers, VISA, the OS and NumPy all produce error strings the
TUI can't control — ``could not open [/dev/ttyUSB0]``, ``[Errno 113] ...``.
Textual and Rich both treat ``[...]`` in a plain string as *markup*, and
a stray ``[/...]`` is a hard ``MarkupError``: raised while a DataTable
renders, that takes the whole app down. So external text is either
escaped for the specific renderer (``rich.markup.escape`` for
``RichLog``, ``textual.markup.escape`` for ``Static``/notifications) or
wrapped in ``rich.text.Text``, which is never parsed.
"""

from __future__ import annotations


def one_line(value: object) -> str:
    """``str(value)`` with every run of whitespace collapsed to one space.

    Driver errors are often multi-line; a table cell shows only one line,
    and a log line reads better without embedded newlines.
    """
    return " ".join(str(value).split())
