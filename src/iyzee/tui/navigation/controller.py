"""Navigation controller skeleton.

The controller owns global navigation state. Event routing is intentionally
kept out of this first commit so existing Textual bindings remain unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .commands import CommandRegistry
from .mode import Mode

if TYPE_CHECKING:
    from textual.app import App


class NavigationController:
    """Own navigation mode and the command registry for an application."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.mode = Mode.NORMAL
        self.commands = CommandRegistry()

    def set_mode(self, mode: Mode) -> None:
        """Set the active navigation mode."""
        self.mode = mode

    def reset(self) -> None:
        """Return navigation to its default global mode."""
        self.mode = Mode.NORMAL
