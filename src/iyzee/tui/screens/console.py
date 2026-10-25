"""Textual's cell-based console backed by an in-process IPython shell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from matplotlib.figure import Figure
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import RichLog, Static, TextArea

from ..ipython import AppState
from ..ipython_controller import IPythonSession
from ..plotting import draw_series, figure_series

if TYPE_CHECKING:
    from ..app import IyzeeApp


class ConsoleScreen(Vertical):
    """The console page and its persistent interactive Python session."""

    def compose(self) -> ComposeResult:
        yield Static("IPython · live lab access · Ctrl+Enter runs a cell", classes="panel-title")
        app = cast("IyzeeApp", self.app)
        yield IyzeeConsole(
            app,
            history_file=app.console_history_file,
            editing_mode=app.console_editing_mode,
        )

    def on_mount(self) -> None:
        console = self.query_one(IyzeeConsole)
        cast("IyzeeApp", self.app).console_session = console.session

    def on_show(self) -> None:
        console = self.query_one(IyzeeConsole)
        console.query_one(TextArea).focus()
        console.refresh_status()


class IyzeeConsole(Vertical):
    """A persistent IPython namespace presented as a code editor and output log."""

    BINDINGS = [
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
    IyzeeConsole #console-editor {
        height: 8; min-height: 4; margin: 0 1; border: round $primary-darken-1;
    }
    IyzeeConsole #console-status { height: 2; padding: 0 2; color: $text-muted; }
    """

    def __init__(
        self,
        app_state: AppState,
        *,
        history_file: str | None = None,
        editing_mode: str = "vi",
    ) -> None:
        super().__init__()
        # Textual handles editing; editing_mode is retained at the app boundary
        # until preference migration is considered separately.
        self._editing_mode = editing_mode
        self._lab_text = ""
        self._busy = False
        self._history: list[str] = []
        self._history_index = 0
        self._history_draft = ""
        self._output: RichLog | None = None
        self.session = IPythonSession(
            app_state,
            history_file=history_file,
            on_output=self._on_output,
            on_busy=self._on_busy,
        )

    def compose(self) -> ComposeResult:
        yield RichLog(id="console-output", wrap=True, markup=False, highlight=False)
        yield TextArea(id="console-editor", show_line_numbers=True)
        yield Static("", id="console-status")

    def on_mount(self) -> None:
        self._output = self.query_one(RichLog)
        self._history = self.session.history()
        self._history_index = len(self._history)

        formatter = self.session.shell.display_formatter
        if formatter is not None:
            formatter.formatters["text/plain"].for_type(Figure, self._render_figure)

        self._write("iyzee IPython console · Ctrl+Enter runs · Ctrl+Space completes")
        self._write("input() is not supported here; define values in the editor instead.\n")
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

    def refresh_status(self) -> None:
        lab = self.session.shell.user_ns.get("lab")
        connected = ", ".join(lab.connected) if lab is not None else ""
        self._lab_text = f"lab: {connected or 'nothing connected yet'}"
        state = "running · Ctrl+C interrupts" if self._busy else (
            "Ctrl+Enter run · Ctrl+Space complete · Ctrl+P/N history"
        )
        try:
            self.query_one("#console-status", Static).update(f"{self._lab_text}  ·  {state}")
        except NoMatches:
            pass

    def action_submit_cell(self) -> None:
        editor = self.query_one(TextArea)
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
        editor = self.query_one(TextArea)
        source = editor.text
        row, column = editor.cursor_location
        before_cursor = "\n".join(source.splitlines()[:row])
        cursor_pos = len(before_cursor) + (1 if row else 0) + column
        matches = self.session.complete(source, cursor_pos)
        if not matches:
            return
        if len(matches) == 1:
            editor.text = source[:cursor_pos] + matches[0] + source[cursor_pos:]
            editor.cursor_location = (row, column + len(matches[0]))
        else:
            self._write("  ".join(matches) + "\n")

    def action_history_previous(self) -> None:
        if self.session.executing or not self._history:
            return
        editor = self.query_one(TextArea)
        if self._history_index == len(self._history):
            self._history_draft = editor.text
        if self._history_index > 0:
            self._history_index -= 1
            self._show_history_entry(editor)

    def action_history_next(self) -> None:
        if self.session.executing or self._history_index >= len(self._history):
            return
        self._history_index += 1
        editor = self.query_one(TextArea)
        if self._history_index == len(self._history):
            editor.text = self._history_draft
        else:
            self._show_history_entry(editor)

    def _show_history_entry(self, editor: TextArea) -> None:
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
        from textual_plotext import PlotextPlot

        plot = self.query_one("#console-plot", PlotextPlot)
        plot.display = True
        draw_series(plot, lines, xlabel=labels["xlabel"] or None, ylabel=labels["ylabel"] or None)
