"""Textual's cell-based console backed by an in-process IPython shell."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from matplotlib.figure import Figure
from rich.text import Text
from textual.app import ComposeResult
from textual import events
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import RichLog, Static
from textual_plotext import PlotextPlot
from textual_vim_textarea import Mode, VimTextArea

from ..ipython import AppState
from ..ipython_controller import IPythonSession
from ..plotting import draw_series, figure_series

if TYPE_CHECKING:
    from ..app import IyzeeApp


def _location_to_offset(text: str, location: tuple[int, int]) -> int:
    row, column = location
    return sum(len(line) + 1 for line in text.split("\n")[:row]) + column


def _offset_to_location(text: str, offset: int) -> tuple[int, int]:
    row = text.count("\n", 0, offset)
    return row, offset - (text.rfind("\n", 0, offset) + 1)


class ConsoleCodeArea(VimTextArea):
    """Vim editor with console-specific Tab completion and cell submission."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "shift+enter":
            console = self._console()
            if console is not None:
                event.stop()
                event.prevent_default()
                console.action_submit_cell()
                return
        if self.mode is Mode.INSERT and event.key == "tab":
            console = self._console()
            if console is not None and console.complete_at_cursor():
                event.stop()
                event.prevent_default()
                return
        await super()._on_key(event)

    def _console(self) -> IyzeeConsole | None:
        return next((node for node in self.ancestors if isinstance(node, IyzeeConsole)), None)


class ConsoleScreen(Vertical):
    """The console page and its persistent interactive Python session."""

    def compose(self) -> ComposeResult:
        yield Static("IPython · Vim editor · Shift+Enter runs a cell", classes="panel-title")
        app = cast("IyzeeApp", self.app)
        yield IyzeeConsole(
            app,
            history_file=app.console_history_file,
        )

    def on_mount(self) -> None:
        console = self.query_one(IyzeeConsole)
        cast("IyzeeApp", self.app).console_session = console.session

    def on_show(self) -> None:
        console = self.query_one(IyzeeConsole)
        console.query_one(VimTextArea).focus()
        console.refresh_status()


class IyzeeConsole(Vertical):
    """A persistent IPython namespace presented as a code editor and output log."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+enter", "submit_cell", "Run cell", priority=True),
        Binding("ctrl+space", "complete", "Complete", show=False, priority=True),
        Binding("ctrl+p", "history_previous", "Previous cell", show=False, priority=True),
        Binding("ctrl+n", "history_next", "Next cell", show=False, priority=True),
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True),
    ]

    DEFAULT_CSS = """
    IyzeeConsole { height: 1fr; min-height: 12; }
    IyzeeConsole #console-output {
        height: 1fr; min-height: 6; margin: 0 1; border: round $primary-darken-1;
    }
    IyzeeConsole #console-plot { height: 12; margin: 0 1; display: none; border: round $primary-darken-1; }
    IyzeeConsole #console-editor {
        height: 8; min-height: 4; margin: 0 1; border: round $primary-darken-1;
    }
    IyzeeConsole #console-status { height: 2; padding: 0 2; color: $text-muted; }
    """

    def __init__(
        self,
        app_state: AppState,
        *,
        history_file: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._lab_text = ""
        self._busy = False
        self._history: list[str] = []
        self._history_index = 0
        self._history_draft = ""
        self._editor_status = "NORMAL"
        self._output: RichLog | None = None
        self.session = IPythonSession(
            app_state,
            history_file=history_file,
            on_output=self._on_output,
            on_busy=self._on_busy,
        )

    def compose(self) -> ComposeResult:
        yield RichLog(id="console-output", wrap=True, markup=False, highlight=False)
        yield ConsoleCodeArea(
            id="console-editor",
            show_line_numbers=True,
            tab_behavior="indent",
            indent_type="spaces",
            indent_width=4,
        )
        yield PlotextPlot(id="console-plot")
        yield Static("", id="console-status")

    def on_mount(self) -> None:
        self._output = self.query_one(RichLog)
        self._history = self.session.history()
        self._history_index = len(self._history)

        formatter = self.session.shell.display_formatter
        if formatter is not None:
            formatter.formatters["text/plain"].for_type(Figure, self._render_figure)

        self._write("iyzee IPython · Vim: i insert, Esc normal, v visual, : commands")
        self._write("Tab completes in INSERT mode; with no matches it indents. Shift+Enter / Ctrl+Enter runs.\n")
        self._write("input(), stdin reads and interactive debugger prompts are not supported.\n")
        self.refresh_status()
        self.session.start()

    def on_unmount(self) -> None:
        if not self.session.close():
            self.app.log.warning(
                "IPython cell did not stop during shutdown; leaving instruments connected"
            )

    def _write(self, text: str) -> None:
        if self._output is not None:
            self._output.write(Text(text), scroll_end=True)

    def _on_output(self, text: str) -> None:
        if self.is_mounted:
            self._write(text)

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.is_mounted:
            self.refresh_status()

    def on_vim_text_area_mode_changed(self, message: VimTextArea.ModeChanged) -> None:
        self._editor_status = message.mode.value
        self.refresh_status()

    def on_vim_text_area_status_changed(self, message: VimTextArea.StatusChanged) -> None:
        self._editor_status = message.status
        self.refresh_status()

    def on_vim_text_area_save_requested(self, _message: VimTextArea.SaveRequested) -> None:
        self._write("The console buffer is not a file; executed cells are stored in IPython history.\\n")

    def on_vim_text_area_quit_requested(self, _message: VimTextArea.QuitRequested) -> None:
        cast("IyzeeApp", self.app).action_request_shutdown()

    def on_vim_text_area_command_entered(self, message: VimTextArea.CommandEntered) -> None:
        cast("IyzeeApp", self.app).run_command(message.command)

    def refresh_status(self) -> None:
        lab = self.session.shell.user_ns.get("lab")
        connected = ", ".join(lab.connected) if lab is not None else ""
        self._lab_text = f"lab: {connected or 'nothing connected yet'}"
        state = "running · Ctrl+C is best-effort" if self._busy else "Tab complete · Shift+Enter run"
        with contextlib.suppress(NoMatches):
            self.query_one("#console-status", Static).update(
                f"{self._lab_text} · {self._editor_status} · {state}"
            )

    def action_submit_cell(self) -> None:
        editor = self.query_one(VimTextArea)
        source = editor.text
        if not source.strip() or self.session.executing:
            return
        self._write(f"In [cell]:\n{source}\n")
        if not self.session.submit(source):
            self._write("A cell is already running.\n")
            return
        self._history.append(source)
        self._history_index = len(self._history)
        self._history_draft = ""
        editor.text = ""

    def action_complete(self) -> None:
        self.complete_at_cursor()

    def complete_at_cursor(self) -> bool:
        editor = self.query_one(VimTextArea)
        source = editor.text
        cursor_pos = _location_to_offset(source, editor.cursor_location)
        matches = self.session.complete(source, cursor_pos)
        if not matches:
            return False
        if len(matches) == 1:
            match = matches[0]
            editor.text = source[: match.start] + match.text + source[match.end :]
            editor.cursor_location = _offset_to_location(editor.text, match.start + len(match.text))
        else:
            self._write("  ".join(m.text.removeprefix(".") for m in matches) + "\n")
        return True

    def action_history_previous(self) -> None:
        if self.session.executing or not self._history:
            return
        editor = self.query_one(VimTextArea)
        if self._history_index == len(self._history):
            self._history_draft = editor.text
        if self._history_index > 0:
            self._history_index -= 1
            self._show_history_entry(editor)

    def action_history_next(self) -> None:
        if self.session.executing or self._history_index >= len(self._history):
            return
        self._history_index += 1
        editor = self.query_one(VimTextArea)
        if self._history_index == len(self._history):
            editor.text = self._history_draft
        else:
            self._show_history_entry(editor)

    def _show_history_entry(self, editor: VimTextArea) -> None:
        editor.text = self._history[self._history_index]
        editor.cursor_location = editor.document.end

    def action_interrupt(self) -> None:
        if self.session.executing:
            self.session.interrupt()

    def _render_figure(self, fig: Figure, printer: Any, cycle: bool) -> None:
        if cycle:
            printer.text("Figure(...)")
            return
        lines, labels = figure_series(fig)
        if not lines:
            printer.text(f"<Figure size {fig.get_size_inches()} with {len(fig.axes)} Axes>")
            return
        printer.text(f"[plotted {len(lines)} line(s) below]")
        self.app.call_from_thread(self._draw_figure, lines, labels)

    def _draw_figure(self, lines: list[tuple[list, list, str]], labels: dict[str, str]) -> None:
        plot = self.query_one("#console-plot", PlotextPlot)
        plot.display = True
        draw_series(plot, lines, xlabel=labels["xlabel"] or None, ylabel=labels["ylabel"] or None)
