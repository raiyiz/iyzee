"""The Console page: a real IPython terminal, inside the app.

What runs here is IPython's own terminal UI — its prompt, editing (vi or
emacs mode), completion menu, history search, auto-suggestions, ``%magics``,
``?`` help, ``%debug`` — in the same process as the app, so ``lab.mx`` is the
live instrument. See ``ipython_session.py`` for how a terminal application is
hosted without a terminal, and ``vterm.py`` / ``terminal_view.py`` for how its
screen is drawn.

This module is the page around it: the plot panel that matplotlib figures are
drawn into, the ``lab: ...`` status line, and the glue.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from IPython.core.displaypub import DisplayPublisher
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static
from textual_plotext import PlotextPlot

from ..ipython import AppState
from ..ipython_session import IPythonSession
from ..plotting import draw_series
from ..terminal_view import TerminalView

if TYPE_CHECKING:
    from pathlib import Path

    from ..app import IyzeeApp


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
        app = cast("IyzeeApp", self.app)
        yield IyzeeConsole(
            app,
            history_file=app.console_history_file,
            editing_mode=app.console_editing_mode,
        )

    def on_mount(self) -> None:
        self.console = self.query_one(IyzeeConsole)
        cast("IyzeeApp", self.app).console_session = self.console.session

    def on_show(self) -> None:
        # Screen.AUTO_FOCUS (what used to put focus on the input every time
        # this screen became active) is Screen-only and doesn't exist for a
        # plain widget, so it's done by hand here instead. Also refresh the
        # status line's "lab: ..." text — nothing about the shell itself
        # needs refreshing.
        self.query_one(TerminalView).focus()
        self.console.refresh_status()


class IyzeeConsole(Vertical):
    """IPython's terminal UI, a plot panel, and a status line."""

    DEFAULT_CSS = """
    /* Scrolls (instead of clipping) once the page is too short for the
       terminal plus the plot panel: with a plot showing, its fixed 12 rows
       would otherwise squeeze the terminal to nothing. min-height keeps the
       terminal usable rather than letting it shrink away. */
    IyzeeConsole { height: 1fr; min-height: 16; overflow-y: auto; }
    IyzeeConsole #console-terminal {
        height: 1fr;
        min-height: 8;
        margin: 0 1 0 1;
    }
    IyzeeConsole #console-plot {
        height: 12;
        margin: 0 1 0 1;
        border: round $primary-darken-1;
        display: none;
    }
    IyzeeConsole #console-status {
        height: 2;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        app_state: AppState,
        *,
        history_file: str | Path | None = None,
        editing_mode: str = "vi",
    ) -> None:
        super().__init__()
        self._lab_text = ""
        self._busy = False
        self._view: TerminalView | None = None
        # Built here (the UI thread, inside compose) because constructing the
        # session needs the running event loop to hand its output back to.
        self.session = IPythonSession(
            app_state,
            history_file=history_file,
            editing_mode=editing_mode,
            on_output=self._on_output,
            on_busy=self._on_busy,
        )

    def compose(self) -> ComposeResult:
        yield TerminalView(id="console-terminal")
        yield PlotextPlot(id="console-plot")
        yield Static("", id="console-status")

    def on_mount(self) -> None:
        self._view = self.query_one(TerminalView)
        self._view.attach(self.session)
        shell = self.session.shell
        shell.display_pub = _ConsoleDisplayPublisher()
        # IPython's terminal shell restricts the display formatter to
        # text/plain (a real terminal can't show the rest). This console can
        # at least turn HTML into text and note images, so let those through.
        formatter = shell.display_formatter
        formatter.active_types = list(formatter.format_types)
        self._install_figure_plotting()
        self.refresh_status()
        # Started only now, with the view in place to receive its output.
        # Deliberately not focusing the terminal here: on_mount fires for every
        # page at app startup (ContentSwitcher mounts all of its children
        # immediately), so this page's on_mount runs even while some other
        # page is the one actually visible. Focusing here would steal focus
        # from whichever page the user is really looking at.
        # ConsoleScreen.on_show() does this instead.
        self.session.start()

    def on_unmount(self) -> None:
        self.session.close()

    # -- session callbacks (UI thread) ----------------------------------------------------

    def _on_output(self, text: str) -> None:
        if self._view is not None:
            self._view.feed(text)

    def _on_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.is_mounted:
            self._render_status()

    # -- status line -------------------------------------------------------------------------

    def refresh_status(self) -> None:
        """Update the "lab: ..." status line. Purely cosmetic — the console's
        `lab` variable itself always reflects current state without needing
        this or any other refresh; see LabProxy."""
        lab = self.session.shell.user_ns.get("lab")
        connected = ", ".join(lab.connected) if lab is not None else ""
        self._lab_text = f"lab: {connected or 'nothing connected yet'}"
        self._render_status()

    def _render_status(self) -> None:
        state = "running… Ctrl+C interrupts" if self._busy else "Ctrl+C clears the line"
        self.query_one("#console-status", Static).update(
            f"{self._lab_text}   ·   {state}   ·   Shift+PageUp/Down or wheel: scrollback"
        )

    # -- matplotlib figures ---------------------------------------------------------------------

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

        formatter = self.session.shell.display_formatter.formatters["text/plain"]
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
        # Runs on the shell's thread, not the UI thread.
        self.app.call_from_thread(self._draw_figure, lines, labels)
        p.text(f"[plotted {len(lines)} line(s) in the plot panel below the terminal]")

    def _draw_figure(self, lines: list[tuple[list, list, str]], labels: dict[str, str]) -> None:
        plot = self.query_one("#console-plot", PlotextPlot)
        plot.display = True
        draw_series(
            plot,
            lines,
            xlabel=labels["xlabel"] or None,
            ylabel=labels["ylabel"] or None,
        )


class _ConsoleDisplayPublisher(DisplayPublisher):
    """Route IPython's rich ``display()`` output into the terminal.

    The base :class:`DisplayPublisher` only ever does anything with the
    ``text/plain`` entry of a display bundle — every other mimetype
    (``text/html`` from a pandas ``DataFrame``, ``image/png`` from a
    matplotlib figure, ...) is silently dropped, so ``display(df)`` degraded
    to a bare ``<DataFrame at 0x...>`` repr with no indication anything was
    lost.

    This renders ``text/html`` as plain text (tags stripped — a real HTML
    renderer is out of scope here) and gives image mimetypes a one-line
    placeholder instead of vanishing, so "a plot was produced" is at least
    visible.

    ``publish`` runs on the shell's thread, whose ``sys.stdout`` is the
    virtual terminal, so this is just ``print``.
    """

    def publish(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if "text/html" in data:
            text = re.sub(r"<[^>]+>", "", data["text/html"]).strip()
            print(text or "(empty)")
            return
        for mime in data:
            if mime.startswith("image/"):
                size = len(data[mime])
                print(
                    f"[{mime}, {size} bytes — inline image display not supported in this console]"
                )
                return
        if "text/plain" in data:
            print(data["text/plain"])
