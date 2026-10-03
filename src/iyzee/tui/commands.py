"""The contextual colon command line used by the iyzee TUI."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from .instruments import INSTRUMENTS


class CommandError(ValueError):
    """A syntactically valid command that cannot be executed."""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    children: tuple[str, ...] = ()


_COMMANDS = (
    CommandSpec("connect", "open Connect or connect an instrument"),
    CommandSpec("disconnect", "disconnect an instrument"),
    CommandSpec("sweep", "open Sweep or run/abort/capture", ("run", "abort", "capture")),
    CommandSpec("scope", "open Scope or sync/acquire", ("sync", "acquire")),
    CommandSpec("results", "open Results"),
    CommandSpec("console", "open the IPython console"),
    CommandSpec("activity", "open application activity"),
    CommandSpec("help", "show command help"),
    CommandSpec("quit", "quit iyzee"),
)


def parse_command(value: str) -> list[str]:
    """Parse a command, accepting colon-prefixed or bare input."""
    value = value.strip()
    if value.startswith(":"):
        value = value[1:].lstrip()
    if not value:
        return []
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise CommandError(f"cannot parse command: {exc}") from exc


def suggestions(value: str) -> list[str]:
    """Return compact command completions for the current line."""
    raw = value.lstrip()
    if raw.startswith(":"):
        raw = raw[1:]
    trailing = raw.endswith(" ")
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()

    if not tokens:
        return [f":{item.name}" for item in _COMMANDS]

    command = tokens[0].lower()
    if len(tokens) == 1 and not trailing:
        return [
            f":{item.name}"
            for item in _COMMANDS
            if item.name.startswith(command) and item.name != command
        ]

    if command in {"connect", "disconnect"} and len(tokens) <= 2:
        prefix = "" if trailing else tokens[-1].lower()
        return [
            f":{command} {spec.key}"
            for spec in INSTRUMENTS
            if spec.key.startswith(prefix)
        ]

    spec = next((item for item in _COMMANDS if item.name == command), None)
    if spec is not None and spec.children and len(tokens) <= 2:
        prefix = "" if trailing else tokens[-1].lower()
        return [
            f":{command} {child}"
            for child in spec.children
            if child.startswith(prefix)
        ]

    return []


def help_text() -> str:
    return "  ".join(f":{item.name} — {item.description}" for item in _COMMANDS)


class CommandInput(Input):
    """Input field that reserves Tab for command completion."""

    BINDINGS = [*Input.BINDINGS, Binding("tab", "complete_command", "Complete", show=False)]

    def action_complete_command(self) -> None:
        parent = self.parent
        command_bar = parent.parent if parent is not None else None
        if isinstance(command_bar, CommandBar):
            command_bar.complete(self)


class CommandBar(Vertical):
    """A small command surface layered over whichever page is active."""

    can_focus = False

    def complete(self, command: Input) -> None:
        """Accept the first completion for the current command line."""
        choices = suggestions(command.value)
        if not choices:
            return
        command.value = choices[0].lstrip(":")
        command.cursor_position = len(command.value)

    def compose(self) -> ComposeResult:
        yield Static("", id="command-hint")
        with Horizontal(id="command-entry"):
            yield Static(":", id="command-prefix")
            yield CommandInput(placeholder="command", id="command-input")

    def open(self, page_id: str) -> None:
        self.add_class("-open")
        self.remove_class("-error")
        self.query_one("#command-hint", Static).update(
            f"[b]Command[/b] · {page_id} · Enter executes · Escape cancels"
        )
        command = self.query_one("#command-input", Input)
        command.value = ""
        command.focus()

    def close(self) -> None:
        self.remove_class("-open")
        self.remove_class("-error")
        self.query_one("#command-input", Input).blur()

    def show_error(self, message: str) -> None:
        self.add_class("-error")
        self.query_one("#command-hint", Static).update(f"[red]{message}[/red]")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command-input":
            return
        self.remove_class("-error")
        choices = suggestions(event.value)
        self.query_one("#command-hint", Static).update(
            "  ".join(choices[:6]) or "Enter to run · Escape to cancel"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command-input":
            return
        try:
            message = self.app.execute_command(event.value)  # type: ignore[attr-defined]
        except CommandError as exc:
            self.show_error(str(exc))
            return
        self.close()
        if message:
            self.app.notify(message, timeout=8, markup=False)  # type: ignore[attr-defined]

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self.has_class("-open"):
            event.stop()
            self.close()
