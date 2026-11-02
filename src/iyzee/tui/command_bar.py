"""The ``:`` command line (and ``/`` search line): opens over the footer.

A ``:`` line runs through ``IyzeeApp.run_command``; a ``/`` line goes to ``IyzeeApp.search``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Input, Static


class CommandInput(Input):
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", show=False, priority=True),
        Binding("tab", "complete", show=False, priority=True),
        Binding("up", "history(-1)", show=False),
        Binding("down", "history(1)", show=False),
    ]

    @property
    def bar(self) -> CommandBar:
        parent = self.parent
        if not isinstance(parent, CommandBar):
            raise RuntimeError("CommandInput must be mounted inside CommandBar")
        return parent

    def action_cancel(self) -> None:
        self.bar.close()

    def action_complete(self) -> None:
        self.bar.complete()

    def action_history(self, step: int) -> None:
        self.bar.history_step(step)


class CommandBar(Horizontal):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._histories: dict[str, list[str]] = {":": [], "/": []}
        self._prefix = ":"
        self._position = 0
        self._previous: Widget | None = None

    def compose(self) -> ComposeResult:
        yield Static(":", classes="cmd-prompt")
        yield CommandInput(id="command-input", compact=True)

    @property
    def history(self) -> list[str]:
        return self._histories[self._prefix]

    @property
    def is_open(self) -> bool:
        return self.has_class("-active")

    def open(self, prefix: str = ":") -> None:
        self._prefix = prefix
        self.query_one(".cmd-prompt", Static).update(prefix)
        self._previous = self.app.focused
        self.add_class("-active")
        field = self.query_one(CommandInput)
        field.value = ""
        self._position = len(self.history)
        field.focus()

    def close(self) -> None:
        self.remove_class("-active")
        previous, self._previous = self._previous, None
        if previous is not None and previous.is_attached:
            previous.focus()

    def complete(self) -> None:
        if self._prefix != ":":
            return
        field = self.query_one(CommandInput)
        line, candidates = self.app.command_registry().complete(field.value)  # type: ignore[attr-defined]
        field.value = line
        field.cursor_position = len(line)
        if len(candidates) > 1:
            self.app.notify("  ".join(candidates), timeout=4, markup=False)

    def history_step(self, step: int) -> None:
        if not self.history:
            return
        self._position = max(0, min(len(self.history), self._position + step))
        field = self.query_one(CommandInput)
        field.value = self.history[self._position] if self._position < len(self.history) else ""
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        line = event.value.strip()
        target = self._previous  # closing hands the focus back, but only a moment later
        self.close()
        if line:
            if not self.history or self.history[-1] != line:
                self.history.append(line)
            if self._prefix == "/":
                self.app.search(line, widget=target)  # type: ignore[attr-defined]
            else:
                self.app.run_command(line)  # type: ignore[attr-defined]
