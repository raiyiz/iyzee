"""Command registry primitives for TUI navigation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


CommandCallback = Callable[[list[str]], None]


@dataclass(frozen=True, slots=True)
class Command:
    """One application command exposed by the navigation layer."""

    name: str
    description: str
    callback: CommandCallback


class CommandRegistry:
    """Small registry that keeps command dispatch separate from navigation."""

    def __init__(self, commands: Iterable[Command] = ()) -> None:
        self._commands: dict[str, Command] = {}
        for command in commands:
            self.register(command)

    def register(self, command: Command) -> None:
        """Register or replace a command by name."""
        if not command.name or command.name.startswith(":"):
            raise ValueError("command names must be non-empty and omit ':'")
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        """Return a command by its canonical name."""
        return self._commands.get(name)

    def names(self) -> tuple[str, ...]:
        """Return registered command names in deterministic order."""
        return tuple(sorted(self._commands))
