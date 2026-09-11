"""Connect screen: open/close links to the lab instruments.

One row per :class:`~iyzee.tui.instruments.InstrumentSpec`. Pressing Enter
on a row connects it if it's not connected, or disconnects it if it is.
Connecting/probing happens in a background thread (``@work(thread=True)``)
since every device call here is blocking I/O; the UI only ever gets
touched back on the main thread via ``call_from_thread``.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..instruments import INSTRUMENTS, InstrumentSpec
from ..workers import ConnectOutcome

STATUS_COL = "status"
DETAIL_COL = "detail"


class ConnectScreen(Screen):
    """Table of instruments with live connect/disconnect status."""

    BINDINGS = [("enter", "toggle_connect", "Connect / Disconnect")]

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

    def _spec_at_cursor(self) -> InstrumentSpec | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return next((spec for spec in INSTRUMENTS if row_key == spec.key), None)

    def action_toggle_connect(self) -> None:
        spec = self._spec_at_cursor()
        if spec is None:
            return
        if spec.key in self.app.handles:
            self._disconnect(spec)
        else:
            self._connect(spec)

    @work(thread=True, exclusive=True, group="connect")
    def _connect(self, spec: InstrumentSpec) -> None:
        self.app.call_from_thread(self._set_row, spec.key, "connecting...", "-")
        try:
            handle = spec.build()
            handle.connect()
            detail = handle.probe()
        except Exception as exc:  # noqa: BLE001 - surfacing to the UI, not swallowing
            self.app.call_from_thread(self._set_row, spec.key, "error", str(exc))
            self.app.call_from_thread(
                self.notify, f"{spec.label}: {exc}", severity="error", timeout=6
            )
            return
        self.app.handles[spec.key] = handle
        outcome = ConnectOutcome(key=spec.key, ok=True, detail=detail)
        self.app.call_from_thread(self._set_row, outcome.key, "connected", outcome.detail)

    @work(thread=True, exclusive=True, group="connect")
    def _disconnect(self, spec: InstrumentSpec) -> None:
        handle = self.app.handles.pop(spec.key, None)
        self.app.call_from_thread(self._set_row, spec.key, "disconnecting...", "-")
        if handle is not None:
            try:
                handle.disconnect()
            except Exception as exc:  # noqa: BLE001
                self.app.call_from_thread(
                    self.notify, f"{spec.label}: error closing ({exc})", severity="warning"
                )
        self.app.call_from_thread(self._set_row, spec.key, "disconnected", "-")

    def _set_row(self, key: str, status: str, detail: str) -> None:
        table = self.query_one(DataTable)
        table.update_cell(key, STATUS_COL, status)
        table.update_cell(key, DETAIL_COL, detail)
