"""Full-screen embedded IPython console: the screen and the console widget
it hosts, kept together since the widget has no other caller."""

from __future__ import annotations

import ctypes
import os
import re
import threading
from typing import TYPE_CHECKING, Any, cast

from IPython.core.displaypub import DisplayPublisher
from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.document._document import Document
from textual.events import Key
from textual.widgets import OptionList, RichLog, Static, TextArea
from textual_plotext import PlotextPlot
from textual_vim_textarea import Mode, VimTextArea

from ..ipython import ExecutionOutput, IyzeeIPython
from ..plotting import draw_series

if TYPE_CHECKING:
    from ..app import IyzeeApp


def _raise_in_thread(thread_id: int, exc_type: type[BaseException]) -> None:
    """Asynchronously raise ``exc_type`` inside the thread identified by
    ``thread_id``.

    There's no stdlib-blessed way to interrupt an arbitrary running
    thread — ordinary threads have no safe cancellation point — so this
    uses CPython's ``PyThreadState_SetAsyncExc``, the same low-level hook
    behind the long-standing "interruptible thread" recipes. The target
    thread only actually raises at its next bytecode boundary, so a call
    blocked entirely inside a C extension with no GIL release point
    (rare for this app's VISA/socket-based instrument calls, which do
    release it) may not stop immediately.
    """
    result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id), ctypes.py_object(exc_type)
    )
    if result > 1:
        # Pending-exception state landed on more than one thread (should
        # never happen with a single valid id) — undo rather than risk
        # corrupting an unrelated thread's state.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)


class ConsoleScreen(Vertical):
    """A live Python/IPython console bound to the app's current instruments."""

    def compose(self) -> ComposeResult:
        yield Static(
            "Embedded IPython • same process • lab.mx / lab.shutter / lab.scope",
            classes="panel-title",
        )
        # Constructed once — this only runs on this page's first mount, not
        # every time it's switched back to, so the shell (and its
        # variables, history, lab) genuinely persists for the app's
        # lifetime. lab itself needs no refreshing either way (see
        # LabProxy's docstring) — it reads app state live on every access.
        #
        # self.app is App[Any] in Textual's stubs; it's always an
        # IyzeeApp at runtime (see ConnectScreen.iyzee_app for why), and
        # that satisfies ipython.AppState structurally.
        yield IyzeeConsole(IyzeeIPython(cast("IyzeeApp", self.app)))

    def on_mount(self) -> None:
        self.console = self.query_one(IyzeeConsole)

    def on_show(self) -> None:
        # Screen.AUTO_FOCUS (what used to put focus on #console-input every
        # time this screen became active) is Screen-only and doesn't exist
        # for a plain widget, so it's done by hand here instead. Also
        # purely cosmetic: refresh the status line's "connected: ..." text
        # — nothing about the shell itself needs refreshing.
        self.query_one("#console-input").focus()
        self.console.refresh_status()


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

    def watch_mode(self, mode: Mode) -> None:
        # VimTextArea's own watch_mode only posts a ModeChanged message;
        # nothing in the UI otherwise shows which mode is active, so a
        # mode you can't see becomes a mode you mistype into. Surface it
        # on the console's status line instead.
        super().watch_mode(mode)
        self.console.set_vim_mode(mode)

    def on_key(self, event: Key) -> None:
        if self.console.completions_visible:
            if event.key in ("up", "down"):
                self.console.move_completion_highlight(-1 if event.key == "up" else 1)
                event.stop()
                return
            if event.key in ("enter", "tab"):
                self.console.accept_highlighted_completion()
                event.stop()
                return
            if event.key == "escape":
                self.console.hide_completions()
                event.stop()
                return
            # Any other keystroke (more typing, backspace, ...) abandons
            # the list rather than trying to keep it in sync char-by-char
            # — Tab reopens it against the new text. Falls through so the
            # key still does its normal thing (e.g. actually types).
            self.console.hide_completions()

        if event.key in ("ctrl+c", "ctrl+l"):
            # Base TextArea already binds ctrl+c to "copy selection", so
            # it never reaches IyzeeConsole's own BINDINGS while this
            # widget has focus — intercepted here instead, same as the
            # two-stage Escape below.
            if event.key == "ctrl+c":
                self.console.action_interrupt()
            else:
                self.console.action_clear()
            event.stop()
            return

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
    IyzeeConsole #console-plot {
        height: 12;
        margin: 0 1 1 1;
        border: round $primary-darken-1;
        display: none;
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
        Binding("ctrl+c", "interrupt", "Interrupt", show=True),
        Binding("ctrl+l", "clear", "Clear", show=True),
    ]

    def __init__(self, shell: IyzeeIPython) -> None:
        super().__init__()
        self.shell = shell
        self._history_cursor: int | None = None
        self._history_draft = ""
        self._lab_text = ""
        # Matches _ConsoleInput's initial Mode.INSERT until the first
        # watch_mode fire (which may happen before this widget is mounted).
        self._mode_label = "-- INSERT --"
        self._executing = False
        self._exec_thread_id: int | None = None
        self._completions: list[str] = []
        self._completion_start = 0

    def compose(self) -> ComposeResult:
        yield RichLog(id="console-output", wrap=True, markup=True, highlight=False)
        yield PlotextPlot(id="console-plot")
        yield OptionList(id="console-completions")
        yield _ConsoleInput(self)
        yield Static("", id="console-status")

    def on_mount(self) -> None:
        self.shell.shell.display_pub = _ConsoleDisplayPublisher(self)
        self._install_figure_plotting()
        self._write_banner()
        self.refresh_status()
        # Deliberately not focusing #console-input here: on_mount now fires
        # for every page at app startup (ContentSwitcher mounts all of its
        # children immediately, unlike the old per-page Screens, which only
        # ever mounted the active one), so this page's on_mount runs even
        # while some other page is the one actually visible. Focusing here
        # would steal focus from whichever page the user is really looking
        # at. ConsoleScreen.on_show() does this instead, since that only
        # fires when this page actually becomes the visible one.

    def _install_figure_plotting(self) -> None:
        """Render matplotlib Figures into the plot panel instead of the
        unhelpful default ``<Figure size ... with N Axes>`` text repr.

        Registered as a ``text/plain`` formatter (not routed through
        ``_ConsoleDisplayPublisher``) because that's the layer that still
        has the actual ``Figure`` object — by the time data reaches a
        ``DisplayPublisher.publish()`` call, matplotlib has already
        flattened it to a mimetype bundle (if it even produced one; a
        bare ``Figure`` without ``%matplotlib inline``-equivalent wiring
        doesn't). Covers this codebase's actual shape — simple line plots,
        the same kind ``experiment.io.build_figure()`` produces — by
        walking ``fig.axes[0].lines``; anything fancier (subplots beyond
        the first axes, imshow, 3D) still falls back to the plain repr.
        """
        from matplotlib.figure import Figure

        formatter = self.shell.shell.display_formatter.formatters["text/plain"]
        formatter.for_type(Figure, self._render_figure)

    def _render_figure(self, fig: Any, p: Any, cycle: bool) -> None:
        # PlainTextFormatter uses IPython.lib.pretty's pretty-printer
        # protocol for registered type printers: func(obj, printer, cycle)
        # writing via `p.text(...)`, not a plain `func(obj) -> str`. Easy
        # to miss since every other formatter (HTML, PNG, ...) just wants
        # a return value.
        if cycle:
            p.text("Figure(...)")
            return
        lines = [
            (list(line.get_xdata()), list(line.get_ydata()), line.get_label())
            for ax in fig.axes
            for line in ax.lines
        ]
        if not lines:
            p.text(f"<Figure size {fig.get_size_inches()} with {len(fig.axes)} Axes>")
            return
        labels = {
            "xlabel": fig.axes[0].get_xlabel() if fig.axes else "",
            "ylabel": fig.axes[0].get_ylabel() if fig.axes else "",
        }
        self.app.call_from_thread(self._draw_figure, lines, labels)
        p.text(f"[plotted {len(lines)} line(s) in the panel above the input]")

    def _draw_figure(self, lines: list[tuple[list, list, str]], labels: dict[str, str]) -> None:
        plot = self.query_one("#console-plot", PlotextPlot)
        plot.display = True
        draw_series(
            plot,
            lines,
            xlabel=labels["xlabel"] or None,
            ylabel=labels["ylabel"] or None,
        )

    def refresh_status(self) -> None:
        """Update the "connected: ..." status line. Purely cosmetic — the
        console's `lab` variable itself always reflects current state
        without needing this or any other refresh; see LabProxy."""
        lab = self.shell.shell.user_ns.get("lab")
        connected = ", ".join(lab.connected) if lab is not None else ""
        self._lab_text = f"lab: {connected or 'nothing connected yet'}"
        self._render_status()

    def set_vim_mode(self, mode: Mode) -> None:
        """Update the vim mode indicator. Called from
        `_ConsoleInput.watch_mode` — see that method for why."""
        self._mode_label = f"-- {mode.value} --"
        if self.is_mounted:
            self._render_status()

    def _render_status(self) -> None:
        running = "   [bold yellow]running… (Ctrl+C to interrupt)[/]" if self._executing else ""
        self._set_status(f"{self._mode_label}   {self._lab_text}{running}")

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
        if self._executing:
            # A second Shift+Enter used to silently cancel the first cell
            # (thanks to `exclusive=True` below) with no feedback at all.
            # Refusing outright is more honest: nothing is lost, and
            # Ctrl+C is the explicit, visible way to actually stop it.
            return
        text_area = self.query_one(TextArea)
        source = text_area.text
        if not source.strip():
            return
        self._history_cursor = None
        self._history_draft = ""
        self.hide_completions()
        self.query_one(RichLog).write(
            f"[bold cyan]In [{self.shell.shell.execution_count}]:[/] {escape(source)}"
        )
        text_area.load_text("")
        self._executing = True
        self._render_status()
        self._execute(source)

    @work(thread=True, exclusive=True, group="ipython", exit_on_error=False)
    def _execute(self, source: str) -> None:
        self._exec_thread_id = threading.get_ident()
        try:
            result = self.shell.execute(source)
        except BaseException as exc:
            # Normally unreachable: IPython's own `run_cell` catches
            # everything raised by the executed code, including
            # KeyboardInterrupt, and reports it through `ExecutionOutput`
            # instead. But `action_interrupt` below delivers that
            # KeyboardInterrupt *asynchronously* (see `_raise_in_thread`),
            # so it can in principle land at a bytecode boundary outside
            # `run_cell`'s own try/except entirely — e.g. while
            # `execute()`'s `contextlib.redirect_stdout` block is
            # unwinding — and escape `shell.execute()` uncaught. Without
            # this fallback that would skip `_finish_execution` below and
            # leave the console stuck showing "running…" forever with no
            # way to submit another cell, since nothing would ever reset
            # `_executing`. Empirically reproducible: hit this exact path
            # while testing Ctrl+C against a `time.sleep()` cell.
            result = ExecutionOutput(
                source=source,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                execution_count=self.shell.shell.execution_count,
                success=False,
            )
        finally:
            self._exec_thread_id = None
        self.app.call_from_thread(self._finish_execution, result)

    def _finish_execution(self, result: ExecutionOutput) -> None:
        self._executing = False
        output = self.query_one(RichLog)
        # IPython formats its own tracebacks (and e.g. `%time` output)
        # with raw ANSI color codes, not Rich markup — pushing that
        # through `escape()` used to dump literal `\x1b[31m...` bytes
        # into the log. `Text.from_ansi` decodes real ANSI SGR sequences
        # into proper Rich styling instead, so error output renders in
        # color like a normal terminal rather than as escape-code noise.
        if result.stdout:
            output.write(Text.from_ansi(result.stdout.rstrip("\n")))
        if result.stderr:
            output.write(Text.from_ansi(result.stderr.rstrip("\n")))
        state = "ok" if result.success else "error"
        self._set_status(f"In [{result.execution_count}]  •  {state}")
        self.query_one(TextArea).focus()

    def action_interrupt(self) -> None:
        """Raise KeyboardInterrupt inside the running cell's worker thread.

        A no-op when nothing is running — there's nothing to interrupt,
        and importantly nothing to accidentally interrupt in some *other*,
        unrelated thread.
        """
        if not self._executing or self._exec_thread_id is None:
            return
        self.query_one(RichLog).write("[dim]— interrupt requested —[/]")
        _raise_in_thread(self._exec_thread_id, KeyboardInterrupt)

    def action_clear(self) -> None:
        """Clear the output log — the console's Ctrl+L, same idea as a
        terminal's ``clear``. Leaves history/namespace/state untouched."""
        self.query_one(RichLog).clear()

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
        prefix, matches = self.shell.complete(source, cursor_pos)
        if not matches:
            self.hide_completions()
            return

        # IPython's own completion contract (see `InteractiveShell.complete`):
        # `prefix` is the exact slice of `source` immediately before the
        # cursor that every entry in `matches` is a full replacement for
        # — e.g. completing "lab.ha" returns prefix=".ha",
        # matches=[".handles"], not "handles". So the replacement span is
        # simply the last `len(prefix)` characters before the cursor; no
        # separate word-boundary heuristic needed (a previous version used
        # one based on whitespace, which is wrong for dotted attribute
        # access like `lab.<Tab>` — it doesn't treat `.` as a boundary).
        token_start = cursor_pos - len(prefix)
        common = os.path.commonprefix(matches)
        if common and common != prefix:
            start = document.get_location_from_index(token_start)
            text_area.replace(common, start, text_area.cursor_location)
            new_cursor = document.get_location_from_index(token_start + len(common))
            text_area.move_cursor(new_cursor)
        self._show_completions(matches, token_start)

    def _show_completions(self, matches: list[str], token_start: int) -> None:
        """Populate the completion list and make it navigable.

        An ``OptionList`` instead of the old static text readout — Up/Down
        move a real highlight (intercepted in ``_ConsoleInput.on_key``
        before either history or vim motions get a look at them, see
        there for why) and Enter/Tab accept the highlighted match, so
        narrowing a long candidate list no longer means retyping it letter
        by letter. ``token_start`` (an index into the source text, not a
        row/col location — it's recomputed against the *current* document
        at accept time) is remembered so accepting a match later replaces
        the right span even after the common-prefix insertion above has
        already moved the cursor.
        """
        self._completions = matches[:200]
        self._completion_start = token_start
        option_list = self.query_one("#console-completions", OptionList)
        option_list.clear_options()
        option_list.add_options(self._completions)
        option_list.highlighted = 0
        option_list.styles.display = "block"

    def hide_completions(self) -> None:
        self._completions = []
        self.query_one("#console-completions", OptionList).styles.display = "none"

    @property
    def completions_visible(self) -> bool:
        return bool(self._completions)

    def move_completion_highlight(self, delta: int) -> None:
        option_list = self.query_one("#console-completions", OptionList)
        count = option_list.option_count
        if count == 0:
            return
        current = option_list.highlighted or 0
        option_list.highlighted = (current + delta) % count

    def accept_highlighted_completion(self) -> None:
        option_list = self.query_one("#console-completions", OptionList)
        index = option_list.highlighted
        if index is None or not self._completions:
            self.hide_completions()
            return
        match = self._completions[index]
        text_area = self.query_one(TextArea)
        document = cast(Document, text_area.document)
        start = document.get_location_from_index(self._completion_start)
        text_area.replace(match, start, text_area.cursor_location)
        new_cursor = document.get_location_from_index(self._completion_start + len(match))
        text_area.move_cursor(new_cursor)
        self.hide_completions()

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


class _ConsoleDisplayPublisher(DisplayPublisher):
    """Route IPython's rich ``display()`` output into the console pane.

    The base :class:`DisplayPublisher` only ever does anything with the
    ``text/plain`` entry of a display bundle (``print(data["text/plain"])``)
    — every other mimetype (``text/html`` from a pandas ``DataFrame``,
    ``image/png`` from a matplotlib figure, ...) is silently dropped, so
    ``display(df)`` degraded to a bare ``<DataFrame at 0x...>`` repr with no
    indication anything was lost.

    This renders ``text/html`` as plain text (tags stripped — a real HTML
    renderer is out of scope here) and gives image mimetypes a one-line
    placeholder instead of vanishing, so "a plot was produced" is at least
    visible. Actually rendering images inline (sixel/kitty graphics
    protocols) needs real-terminal verification this pass doesn't have
    budget for — tracked as a follow-up in
    ``docs/console-improvements-plan.md``.

    Runs inside the execution worker thread (``IyzeeConsole._execute``),
    not the UI thread, so writes are marshalled via ``call_from_thread``
    like every other cross-thread UI update in this widget. Note this
    writes to the output log *immediately*, whereas a cell's own
    stdout/stderr is only flushed once the whole cell finishes (see
    ``execute()`` in ``ipython.py``) — a ``print()`` before a ``display()``
    call in the same cell will visibly appear *after* it. Fixing that
    ordering means streaming stdout live too (plan item #4); not attempted
    here.
    """

    def __init__(self, console: IyzeeConsole) -> None:
        super().__init__()
        self.console = console

    def publish(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.console.app.call_from_thread(self._write, data)

    def _write(self, data: dict[str, Any]) -> None:
        output = self.console.query_one(RichLog)
        if "text/html" in data:
            text = re.sub(r"<[^>]+>", "", data["text/html"]).strip()
            output.write(escape(text) if text else "[dim](empty)[/]")
            return
        for mime in data:
            if mime.startswith("image/"):
                size = len(data[mime])
                output.write(
                    f"[dim]\\[{mime}, {size} bytes — inline image display "
                    "not supported in this console][/]"
                )
                return
        if "text/plain" in data:
            output.write(escape(data["text/plain"]))
