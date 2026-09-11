"""A Textual frontend for an embedded IPython shell."""

from __future__ import annotations

import os
from collections.abc import Mapping

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, RichLog, Static, TextArea

from ..ipython import ExecutionOutput, IyzeeIPython


class IyzeeConsole(Vertical):
    """Interactive IPython pane with completion, history, and output."""

    DEFAULT_CSS = """
    IyzeeConsole { height: 1fr; min-height: 10; }
    IyzeeConsole #console-output {
        height: 1fr;
        border: round $primary-darken-1;
        margin: 0 1 1 1;
    }
    IyzeeConsole #console-input {
        height: 7;
        margin: 0 1;
        border: round $primary;
    }
    IyzeeConsole #console-status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
    }
    IyzeeConsole #console-completions {
        height: auto;
        max-height: 8;
        margin: 0 1;
        padding: 0 1;
        border: round $primary-darken-1;
        display: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+enter", "execute", "Run", show=True),
        Binding("ctrl+p", "history_previous", "History ↑", show=True),
        Binding("ctrl+n", "history_next", "History ↓", show=True),
        Binding("tab", "complete", "Complete", show=False),
    ]

    def __init__(self, shell: IyzeeIPython, *, namespace: Mapping[str, object] | None = None) -> None:
        super().__init__()
        self.shell = shell
        self._namespace = dict(namespace or {})
        self._history_cursor: int | None = None
        self._history_draft = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="console-output", wrap=True, markup=True, highlight=False)
        yield Static("", id="console-completions")
        yield TextArea(
            id="console-input",
            placeholder="Python / IPython code  •  Ctrl+Enter to run  •  Tab to complete",
            soft_wrap=True,
            compact=True,
        )
        yield Static("", id="console-status")
        yield Footer()

    def on_mount(self) -> None:
        self._write_banner()
        self.query_one(TextArea).focus()

    def refresh_namespace(self, namespace: Mapping[str, object]) -> None:
        """Refresh live application objects without deleting user variables."""
        self._namespace = dict(namespace)
        self.shell.update_namespace(namespace)
        names = ", ".join(sorted(name for name in namespace if not name.startswith("_")))
        self._set_status(f"live namespace: {names or 'IPython only'}")

    def _write_banner(self) -> None:
        output = self.query_one(RichLog)
        output.write("[bold cyan]iyzee IPython console[/]")
        output.write("Live Python access: mx / shutter / scope appear when connected.")
        output.write(
            "IPython features: Tab completion, ?, ??, %, !, history, and top-level await."
        )
        output.write("Ctrl+Enter executes the current cell; Ctrl+P/Ctrl+N browse IPython history.")

    def _set_status(self, text: str) -> None:
        self.query_one("#console-status", expect_type=Static).update(text)

    def action_execute(self) -> None:
        text_area = self.query_one(TextArea)
        source = text_area.text
        if not source.strip():
            return
        self._history_cursor = None
        self._history_draft = ""
        self._hide_completions()
        self.query_one(RichLog).write(
            f"[bold cyan]In [{self.shell.shell.execution_count}]:[/] {escape(source)}"
        )
        text_area.load_text("")
        self._execute(source)

    @work(thread=True, exclusive=True, group="ipython", exit_on_error=False)
    def _execute(self, source: str) -> None:
        result = self.shell.execute(source)
        self.app.call_from_thread(self._finish_execution, result)

    def _finish_execution(self, result: ExecutionOutput) -> None:
        output = self.query_one(RichLog)
        if result.stdout:
            output.write(escape(result.stdout.rstrip("\n")))
        if result.stderr:
            output.write(escape(result.stderr.rstrip("\n")))
        state = "ok" if result.success else "error"
        self._set_status(f"In [{result.execution_count}]  •  {state}")
        self.query_one(TextArea).focus()

    def action_complete(self) -> None:
        text_area = self.query_one(TextArea)
        source = text_area.text
        cursor_pos = text_area.document.get_index_from_location(text_area.cursor_location)
        _completed, matches = self.shell.complete(source, cursor_pos)
        if not matches:
            self._hide_completions()
            return

        common = os.path.commonprefix(matches)
        token_start = self._completion_token_start(source, cursor_pos)
        current = source[token_start:cursor_pos]
        if common and common != current:
            start = text_area.document.get_location_from_index(token_start)
            text_area.replace(common, start, text_area.cursor_location)
            new_cursor = text_area.document.get_location_from_index(token_start + len(common))
            text_area.move_cursor(new_cursor)
        self._show_completions(matches, common)

    @staticmethod
    def _completion_token_start(source: str, cursor_pos: int) -> int:
        prefix = source[:cursor_pos]
        return max(prefix.rfind(" "), prefix.rfind("\n"), prefix.rfind("\t")) + 1

    def _show_completions(self, matches: list[str], common: str) -> None:
        shown = matches[:24]
        suffix = f"  •  common prefix: {escape(common)}" if common else ""
        lines = "\n".join(escape(match) for match in shown)
        if len(matches) > len(shown):
            lines += f"\n… and {len(matches) - len(shown)} more"
        widget = self.query_one("#console-completions", expect_type=Static)
        widget.update(lines + suffix)
        widget.styles.display = "block"

    def _hide_completions(self) -> None:
        self.query_one("#console-completions", expect_type=Static).styles.display = "none"

    def action_history_previous(self) -> None:
        history = self.shell.history
        if not history:
            return
        if self._history_cursor is None:
            self._history_draft = self.query_one(TextArea).text
            self._history_cursor = len(history)
        self._history_cursor = max(0, self._history_cursor - 1)
        self.query_one(TextArea).load_text(history[self._history_cursor])

    def action_history_next(self) -> None:
        if self._history_cursor is None:
            return
        history = self.shell.history
        self._history_cursor += 1
        if self._history_cursor >= len(history):
            self._history_cursor = None
            self.query_one(TextArea).load_text(self._history_draft)
        else:
            self.query_one(TextArea).load_text(history[self._history_cursor])
