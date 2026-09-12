"""Pure keyboard-ownership policy for the TUI."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Input, TextArea


class NavigationPolicy:
    """Answer whether a focused widget owns text-editing keys."""

    @staticmethod
    def is_insert(widget: Widget | None) -> bool:
        """Return whether ``widget`` should receive editing keys first."""
        if widget is None:
            return False
        return isinstance(widget, (Input, TextArea)) or bool(
            getattr(widget, "is_editable", False)
        )
