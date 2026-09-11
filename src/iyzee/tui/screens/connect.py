"""Connect screen: open/close links to the lab instruments.

One row per :class:`~iyzee.tui.instruments.InstrumentSpec`. Pressing Enter
on a row connects it if it's not connected, or disconnects it if it is.
Connecting/probing happens in a background thread (``@work(thread=True)``)
since every device call here is blocking I/O; the UI only ever gets
touched back on the main thread via ``call_from_thread``.
"""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..instruments import INSTRUMENTS, InstrumentSpec
from ..workers import ConnectOutcome

log = logging.getLogger("iyzee.tui")

STATUS_COL = "status"
DETAIL_COL = "detail"


class ConnectScreen(Screen):
    """Table of instruments with live connect/disconnect status."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Instruments", classes="panel-title"),
            Static("Enter: connect/disconnect selected row", classes="hint"),
            DataTable(id="instrument-table", cursor_type="row"),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Instrument", key="instrument")
        table.add_column("Status", key=STATUS_COL)
        table.add_column("Detail", key=DETAIL_COL)
        for spec in INSTRUMENTS:
            table.add_row(spec.label, "disconnected", "-", key=spec.key)
        self._refresh_from_app_state()

    def on_screen_resume(self) -> None:
        # Reflect connections made/dropped while this screen wasn't visible
        # (there's currently no other way to (dis)connect, but this keeps
        # the table honest if that changes later).
        self._refresh_from_app_state()

    def _refresh_from_app_state(self) -> None:
        table = self.query_one(DataTable)
        for spec in INSTRUMENTS:
            if spec.key in self.app.handles:
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
        if spec.key in self.app.handles:
            self._disconnect(spec)
        else:
            self._connect(spec)

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

    @work(thread=True, exclusive=True, group="connect", exit_on_error=False)
    def _connect(self, spec: InstrumentSpec) -> None:
        self._ui(self._set_row, spec.key, "connecting...", "-")
        try:
            handle = spec.build()
            handle.connect()
            detail = handle.probe()
        except Exception as exc:  # noqa: BLE001 - surfacing to the UI, not swallowing
            log.exception("failed to connect %s", spec.key)
            self._ui(self._set_row, spec.key, "error", str(exc))
            self._ui(self.notify, f"{spec.label}: {exc}", severity="error", timeout=6)
            return
        self.app.handles[spec.key] = handle
        outcome = ConnectOutcome(key=spec.key, ok=True, detail=detail)
        self._ui(self._set_row, outcome.key, "connected", outcome.detail)

    @work(thread=True, exclusive=True, group="connect", exit_on_error=False)
    def _disconnect(self, spec: InstrumentSpec) -> None:
        handle = self.app.handles.pop(spec.key, None)
        self._ui(self._set_row, spec.key, "disconnecting...", "-")
        if handle is not None:
            try:
                handle.disconnect()
            except Exception as exc:  # noqa: BLE001
                log.exception("error closing %s", spec.key)
                self._ui(self.notify, f"{spec.label}: error closing ({exc})", severity="warning")
        self._ui(self._set_row, spec.key, "disconnected", "-")

    def _set_row(self, key: str, status: str, detail: str) -> None:
        table = self.query_one(DataTable)
        table.update_cell(key, STATUS_COL, status)
        table.update_cell(key, DETAIL_COL, detail)
