"""Connect screen: open/close links to the lab instruments.

One row per :class:`~iyzee.tui.instruments.InstrumentSpec`. Pressing Enter
on a row connects it if it's not connected, or disconnects it if it is.
Connecting/probing happens in a background thread (``@work(thread=True)``)
since every device call here is blocking I/O; the UI only ever gets
touched back on the main thread via ``call_from_thread``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from ..instruments import INSTRUMENTS, InstrumentSpec
from ..text import one_line
from ..workers import ConnectOutcome
from .page import Page

if TYPE_CHECKING:
    from ..app import IyzeeApp

log = logging.getLogger("iyzee.tui")

STATUS_COL = "status"
DETAIL_COL = "detail"


class ConnectScreen(Page):
    """Table of instruments with live connect/disconnect status."""

    @property
    def iyzee_app(self) -> IyzeeApp:
        """``self.app`` narrowed to the concrete app type.

        ``Widget.app`` is typed as ``App[Any]`` in Textual's stubs, which
        doesn't know about ``handles``/``instrument_locks`` — this app is
        always an ``IyzeeApp`` at runtime (``IyzeeApp().run()`` is the
        only entry point), so the cast is safe.
        """
        return cast("IyzeeApp", self.app)

    def compose(self) -> ComposeResult:
        yield Static("Instruments", classes="panel-title")
        yield Static("Enter: connect/disconnect selected row", classes="hint")
        yield DataTable(id="instrument-table", cursor_type="row")

    def on_mount(self) -> None:
        # Keys of instruments with a connect/disconnect in flight. Enter on
        # such a row is ignored: a second connect used to open the device
        # a second time and overwrite (leak) the first handle.
        self._busy: set[str] = set()
        table = self.query_one(DataTable)
        table.add_column("Instrument", key="instrument")
        table.add_column("Status", key=STATUS_COL)
        table.add_column("Detail", key=DETAIL_COL)
        for spec in INSTRUMENTS:
            table.add_row(spec.label, "disconnected", "-", key=spec.key)
        self._refresh_from_app_state()

    def on_show(self) -> None:
        # Reflect connections made/dropped while this page wasn't visible
        # (there's currently no other way to (dis)connect, but this keeps
        # the table honest if that changes later).
        self._refresh_from_app_state()

    def _refresh_from_app_state(self) -> None:
        table = self.query_one(DataTable)
        for spec in INSTRUMENTS:
            if spec.key in self.iyzee_app.handles:
                table.update_cell(spec.key, STATUS_COL, "connected")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # NOTE: this is the correct hook for "Enter pressed on a row" — a
        # Screen-level `BINDINGS = [("enter", ...)]` does *not* work here,
        # because DataTable binds Enter to its own `select_cursor` action
        # internally and consumes the key before it would ever bubble up
        # to the screen. RowSelected is what DataTable posts as a result
        # of that internal action; listen for the message, not the key.
        if event.data_table.id != "instrument-table":
            return
        spec = next((s for s in INSTRUMENTS if event.row_key == s.key), None)
        if spec is None:
            return
        if spec.key in self._busy:
            self.notify(
                f"{spec.label} is still busy — wait for it to finish.",
                severity="warning",
                timeout=3,
                markup=False,
            )
            return
        self._busy.add(spec.key)
        if spec.key in self.iyzee_app.handles:
            self._disconnect(spec)
        else:
            self._connect(spec)

    def _release(self, key: str) -> None:
        self._busy.discard(key)

    def _ui(self, callback, *args, **kwargs) -> None:
        """Call back into the UI thread from a worker, without ever letting
        that call blow up the worker itself.

        If the user has switched screens (or the table has been torn down
        for some other reason) by the time a background connect/disconnect
        finishes, the widget this tries to update may be gone. Textual
        propagates any exception raised inside a ``call_from_thread``
        callback back to the calling (worker) thread; left unguarded, that
        turns an ordinary "switched screens mid-connect" moment into an
        unhandled worker exception, which is a much worse failure mode
        than just skipping a now-irrelevant UI update.
        """
        try:
            self.app.call_from_thread(callback, *args, **kwargs)
        except Exception:
            log.exception("connect screen: UI update from worker thread failed")

    # Not ``exclusive``: that cancels the previous worker in the group, so
    # connecting a second instrument used to "cancel" the first one's
    # worker mid-connect. Per-instrument re-entry is prevented by
    # ``_busy`` instead, and different instruments may connect in parallel.
    @work(thread=True, group="connect", exit_on_error=False)
    def _connect(self, spec: InstrumentSpec) -> None:
        try:
            self._ui(self._set_row, spec.key, "connecting...", "-")
            try:
                with self.iyzee_app.instrument_locks[spec.key]:
                    handle = spec.build()
                    try:
                        handle.connect()
                        detail = handle.probe()
                    except Exception:
                        # connect() may have half-succeeded (or probe() failed
                        # after it did); close the link rather than leak it,
                        # since nothing will ever hold a reference to it.
                        with contextlib.suppress(Exception):
                            handle.disconnect()
                        raise
            except Exception as exc:  # noqa: BLE001 - surfacing to the UI, not swallowing
                log.exception("failed to connect %s", spec.key)
                self._ui(self._set_row, spec.key, "error", one_line(exc))
                self._ui(
                    self.notify,
                    f"{spec.label}: {one_line(exc)}",
                    severity="error",
                    timeout=6,
                    markup=False,
                )
                return
            self.iyzee_app.handles[spec.key] = handle
            outcome = ConnectOutcome(key=spec.key, ok=True, detail=detail)
            self._ui(self._set_row, outcome.key, "connected", outcome.detail)
        finally:
            self._ui(self._release, spec.key)

    @work(thread=True, group="connect", exit_on_error=False)
    def _disconnect(self, spec: InstrumentSpec) -> None:
        try:
            handle = self.iyzee_app.handles.pop(spec.key, None)
            self._ui(self._set_row, spec.key, "disconnecting...", "-")
            if handle is not None:
                try:
                    with self.iyzee_app.instrument_locks[spec.key]:
                        handle.disconnect()
                except Exception as exc:  # noqa: BLE001
                    log.exception("error closing %s", spec.key)
                    self._ui(
                        self.notify,
                        f"{spec.label}: error closing ({one_line(exc)})",
                        severity="warning",
                        markup=False,
                    )
            self._ui(self._set_row, spec.key, "disconnected", "-")
        finally:
            self._ui(self._release, spec.key)

    def _set_row(self, key: str, status: str, detail: str) -> None:
        table = self.query_one(DataTable)
        # A plain string in a cell is parsed as markup, and driver messages
        # like "could not open [/dev/ttyUSB0]" then raise a MarkupError
        # inside DataTable's render — which takes the whole app down. So the
        # detail (external text) is escaped. Escaped rather than wrapped in
        # Text so the stored cell value stays an ordinary str.
        table.update_cell(key, STATUS_COL, status)
        table.update_cell(key, DETAIL_COL, escape(one_line(detail)))
