"""Vim-style ``:`` commands: parsing, lookup and completion. No Textual in here.

A :class:`Command` is a name, a handler taking the argument words, and a
one-line help. Several sources contribute to one :class:`CommandRegistry`: the
app's global commands, then whatever the current page offers (see
``Page.commands``); a page command shadows a global one of the same name.

Lookup is vim-like: an exact name or alias wins, otherwise a *unique* prefix
does (``:conn`` for ``:connect``); an ambiguous prefix is an error that lists
the candidates.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass


class CommandError(Exception):
    """A user-facing problem with a command line (shown as-is)."""


@dataclass(frozen=True)
class Command:
    name: str
    run: Callable[[list[str]], str | None]
    help: str
    usage: str = ""  # argument synopsis for :help, e.g. "<instrument>..."
    complete: Callable[[str], Sequence[str]] | None = None  # candidates for an argument prefix
    aliases: tuple[str, ...] = ()


_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_SI_RE = re.compile(
    r"^(?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<prefix>[pnuµmkKMG]?)(?:s|S|V|Hz)?$"
)


def parse_si(text: str) -> float:
    """``"2u"``, ``"500ns"``, ``"1.5m"``, ``"1e-6"`` -> float (``m`` is milli, ``M`` is mega)."""
    match = _SI_RE.match(text.strip())
    if match is None:
        raise CommandError(f"not a number: {text!r} (try 2u, 500n, 1.5m, 1e-6)")
    return float(match["num"]) * _SI[match["prefix"]]


class CommandRegistry:
    def __init__(self, commands: Iterable[Command] = ()) -> None:
        self._by_word: dict[str, Command] = {}
        self._commands: dict[str, Command] = {}
        self.add(*commands)

    def add(self, *commands: Command) -> None:
        """Register commands; a later one replaces an earlier one with the same name or alias."""
        for command in commands:
            self._commands[command.name] = command
            for word in (command.name, *command.aliases):
                self._by_word[word] = command

    @property
    def names(self) -> list[str]:
        return sorted(self._commands)

    def find(self, word: str) -> Command:
        if word in self._by_word:
            return self._by_word[word]
        matches = [name for name in self.names if name.startswith(word)]
        if len(matches) == 1:
            return self._commands[matches[0]]
        if matches:
            raise CommandError(f"ambiguous :{word} - could be {', '.join(matches)}")
        raise CommandError(f"unknown command :{word}  (:help lists what is available here)")

    @staticmethod
    def _split(line: str) -> list[str]:
        try:
            return shlex.split(line.strip().removeprefix(":"))
        except ValueError as exc:
            raise CommandError(f"cannot parse command: {exc}") from exc

    def execute(self, line: str) -> str | None:
        """Run a command line (with or without the leading ``:``). Returns its message, if any."""
        words = self._split(line)
        if not words:
            return None
        return self.find(words[0]).run(words[1:])

    def complete(self, line: str) -> tuple[str, list[str]]:
        """Tab completion: ``(new line, candidates)``; the line is unchanged when nothing fits."""
        try:
            words = self._split(line)
        except CommandError:
            return line, []
        if not words or (len(words) == 1 and not line.endswith(" ")):
            prefix = words[0] if words else ""
            return self._finish(line, prefix, [n for n in self.names if n.startswith(prefix)])
        try:
            command = self.find(words[0])
        except CommandError:
            return line, []
        if command.complete is None:
            return line, []
        prefix = "" if line.endswith(" ") else words[-1]
        used = set(words[1:-1] if prefix else words[1:])
        options = [c for c in command.complete(prefix) if c.startswith(prefix) and c not in used]
        return self._finish(line, prefix, options)

    @staticmethod
    def _finish(line: str, prefix: str, options: list[str]) -> tuple[str, list[str]]:
        if not options:
            return line, []
        head = line[: len(line) - len(prefix)]
        if len(options) == 1:
            return head + options[0] + " ", options
        return head + os.path.commonprefix(options), options

    def help_text(self, word: str | None = None) -> str:
        if word:
            command = self.find(word)
            names = ", ".join(f":{n}" for n in (command.name, *command.aliases))
            usage = f" {command.usage}" if command.usage else ""
            return f"{names}{usage}\n{command.help}"
        lines = []
        for name in self.names:
            command = self._commands[name]
            label = f":{name} {command.usage}".rstrip()
            lines.append(f"{label:<28} {command.help}")
        return "\n".join(lines)
