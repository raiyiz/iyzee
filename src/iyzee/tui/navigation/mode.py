"""Navigation modes used by the Iyzee TUI."""

from enum import Enum


class Mode(str, Enum):
    """High-level ownership of keyboard input."""

    NORMAL = "normal"
    INSERT = "insert"
    COMMAND = "command"
