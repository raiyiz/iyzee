"""A Textual frontend for an embedded IPython shell."""

from __future__ import annotations

import os
from typing import cast

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.document._document import Document
from textual.events import Key
from textual.widgets import Footer, RichLog, Static, TextArea
from textual_vim_textarea import Mode, VimTextArea

from ..ipython import ExecutionOutput, IyzeeIPython


class _ConsoleInput(VimTextArea):
    """IPython editor: real vim modal editing, plus history navigation and
    an escape hatch back to the app's own navigation.

    Vim's motions, operators, counts, registers, and command line all come
    from ``textual_vim_textarea.VimTextArea`` unmodified (see that
    package's own docs for the full key set) — this subclass only adds two
    things the base widget doesn't know about: IPython history on Up/Down,
    and a second Escape to leave the widget entirely.

    Escape is two-stage by design, not an oversight:

    - 1st Escape (from INSERT): handled *inside* VimTextArea itself,
      which transitions INSERT -> NORMAL and keeps focus. Our own
      ``on_key`` below never even sees the "after" state for this
      keystroke — Textual calls a widget's public ``on_key`` before its
      internal ``_on_key`` (which is what VimTextArea overrides to
      implement the transition), so at the moment our check runs,
      ``self.mode`` still reads INSERT. That ordering was verified
      directly against textual-vim-textarea 1.2.0, not assumed.
    - 2nd Escape (already NORMAL): our check now sees NORMAL and blurs,
      handing focus back to Textual's app-level bindings (the same
      navigation shown in the Footer and command palette).

    This mirrors how nested modal contexts are usually resolved elsewhere
    (e.g. Neovim's terminal mode needs its own escape *out* of terminal
    input before window/pane navigation applies) — a single Escape can't
    mean both things at once without breaking one of them.
    """

    def __init__(self, console: IyzeeConsole) -> None:
        super().__init__(
            id="console-input",
            placeholder="Python / IPython code  •  Shift+Enter to run  •  Tab to complete",
            soft_wrap=True,
            compact=True,
        )
        self.console = console
        # Start ready to type: this is a REPL first, a vim buffer second.
        # Escape still reaches full vim NORMAL mode (motions, operators,
        # ':' command line, ...) whenever it's wanted.
        self.mode = Mode.INSERT

    def on_key(self, event: Key) -> None:
        if event.key == "escape" and self.mode is Mode.NORMAL:
            self.blur()
            event.stop()
            return

        if self.mode is not Mode.INSERT:
            # Deliberately not offering history browsing from NORMAL mode:
            # up/down there are vim cursor motions, not REPL history, to
            # keep the two mental models from bleeding into each other.
            return

        cursor_row = self.cursor_location[0]
        if event.key == "up" and cursor_row == 0:
            if self.console.history_available:
                self.console.action_history_previous()
                event.stop()
                return
        elif event.key == "down" and cursor_row == self.document.line_count - 1:
            if self.console.history_cursor is not None:
                self.console.action_history_next()
                event.stop()
                return


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
        Binding("shift+enter", "execute", "Run", show=True),
        Binding("ctrl+p", "history_previous", "History ↑", show=True),
        Binding("ctrl+n", "history_next", "History ↓", show=True),
        Binding("tab", "complete", "Complete", show=False),
    ]

    def __init__(self, shell: IyzeeIPython) -> None:
        super().__init__()
        self.shell = shell
        self._history_cursor: int | None = None
        self._history_draft = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="console-output", wrap=True, markup=True, highlight=False)
        yield Static("", id="console-completions")
        yield _ConsoleInput(self)
        yield Static("", id="console-status")
        yield Footer()

    def on_mount(self) -> None:
        self._write_banner()
        self.refresh_status()
        self.query_one(TextArea).focus()

    def refresh_status(self) -> None:
        """Update the "connected: ..." status line. Purely cosmetic — the
        console's `lab` variable itself always reflects current state
        without needing this or any other refresh; see LabProxy."""
        lab = self.shell.shell.user_ns.get("lab")
        connected = ", ".join(lab.connected) if lab is not None else ""
        self._set_status(f"lab: {connected or 'nothing connected yet'}")

    def _write_banner(self) -> None:
        output = self.query_one(RichLog)
        output.write("[bold cyan]iyzee IPython console[/]")
        output.write("Live Python access: lab.mx / lab.shutter / lab.scope when connected.")
        output.write("lab.results is the last completed sweep's StepResult list.")
        output.write("IPython features: Tab completion, ?, ??, %, !, history, and top-level await.")
        output.write(
            "Shift+Enter executes the current cell; ↑/↓ or Ctrl+P/Ctrl+N browse IPython history."
        )

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
        # TextArea.document is typed as the abstract DocumentBase (it could
        # in principle be a custom document type), but TextArea only ever
        # constructs a concrete Document (or SyntaxAwareDocument, which
        # subclasses it) — see textual.widgets._text_area.TextArea.__init__.
        # The offset<->location helpers below live on that concrete type.
        document = cast(Document, text_area.document)
        cursor_pos = document.get_index_from_location(text_area.cursor_location)
        _completed, matches = self.shell.complete(source, cursor_pos)
        if not matches:
            self._hide_completions()
            return

        common = os.path.commonprefix(matches)
        token_start = self._completion_token_start(source, cursor_pos)
        current = source[token_start:cursor_pos]
        if common and common != current:
            start = document.get_location_from_index(token_start)
            text_area.replace(common, start, text_area.cursor_location)
            new_cursor = document.get_location_from_index(token_start + len(common))
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

    @property
    def history_available(self) -> bool:
        """Whether the IPython session has history entries to browse."""
        return bool(self.shell.history)

    @property
    def history_cursor(self) -> int | None:
        """Current history index, or ``None`` when not browsing history."""
        return self._history_cursor

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
