"""LeCroy scope view embedded in the main TUI shell."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from ..scope import Waveform
from .devices import DeviceManager
from .scope_plot import ScopeTracePlot


class ScopeScreen(Widget):
    """Right-hand page for safe LeCroy connection, inspection, and acquisition."""

    DEFAULT_CSS = """
    ScopeScreen {
        width: 100%;
        height: 100%;
    }

    #scope-body {
        width: 100%;
        height: 1fr;
    }

    #scope-controls {
        width: 32;
        padding: 1;
        border: round $primary;
    }

    #scope-main {
        width: 1fr;
        padding-left: 2;
    }

    .page-header {
        height: auto;
        margin-bottom: 1;
    }

    .page-title {
        text-style: bold;
    }

    .muted {
        color: $text-muted;
    }

    .section-title {
        text-style: bold;
        margin: 0 0 1 0;
    }

    .control-row {
        height: auto;
        margin-bottom: 1;
    }

    .control-row Input {
        width: 1fr;
        margin-right: 1;
    }

    #scope-status,
    #scope-idn {
        height: auto;
        min-height: 3;
        border: round $secondary;
        padding: 1;
        margin-bottom: 1;
    }

    #scope-plot {
        height: 1fr;
        min-height: 18;
        border: round $primary;
    }
    """

    def __init__(self, devices: DeviceManager) -> None:
        super().__init__()
        self.devices = devices
        self._busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="page-header"):
            yield Label("LECROY SCOPE", classes="page-title")
            yield Static("  /  single-waveform inspection", classes="muted")

        with Horizontal(id="scope-body"):
            with Vertical(id="scope-controls"):
                yield Label("Connection", classes="section-title")
                yield Static("○ Disconnected", id="scope-connection")
                with Horizontal(classes="control-row"):
                    yield Button("Connect", id="scope-connect", variant="primary")
                    yield Button("Disconnect", id="scope-disconnect")

                yield Label("Identity", classes="section-title")
                yield Button("Read ID", id="scope-idn-read")

                yield Label("Acquisition", classes="section-title")
                with Horizontal(classes="control-row"):
                    yield Input("C1", id="scope-channel", placeholder="C1")
                    yield Button("Acquire", id="scope-acquire", variant="success")

                yield Static(
                    "The scope uses its current front-panel acquisition configuration.",
                    classes="muted",
                )

            with Vertical(id="scope-main"):
                yield Static("Ready — scope not connected", id="scope-status")
                yield Static("ID: not queried", id="scope-idn")
                yield ScopeTracePlot(id="scope-plot")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget_id in (
            "scope-connect",
            "scope-disconnect",
            "scope-idn-read",
            "scope-acquire",
        ):
            self.query_one(f"#{widget_id}", Button).disabled = busy
        self.query_one("#scope-channel", Input).disabled = busy

    def _set_connection(self, text: str) -> None:
        self.query_one("#scope-connection", Static).update(text)

    def _set_status(self, text: str) -> None:
        self.query_one("#scope-status", Static).update(text)

    def _set_idn(self, text: str) -> None:
        self.query_one("#scope-idn", Static).update(text)

    def _operation_failed(self, text: str) -> None:
        self._set_busy(False)
        self._set_status(text)

    @work(thread=True)
    def _connect_worker(self) -> None:
        try:
            scope = self.devices.connect_scope()
        except Exception as exc:
            self.app.call_from_thread(
                self._operation_failed, f"Connection failed\n{exc}"
            )
            self.app.call_from_thread(self._set_connection, "× Connection failed")
            return
        self.app.call_from_thread(self._set_busy, False)
        self.app.call_from_thread(self._set_connection, "● Connected")
        self.app.call_from_thread(
            self._set_status,
            f"Scope connected\n{scope.ip}:{scope.LECROY_SERVER_PORT}",
        )

    @work(thread=True)
    def _identify_worker(self) -> None:
        try:
            with self.devices.scope_for_operation() as scope:
                identity = scope.identify()
        except Exception as exc:
            self.app.call_from_thread(self._operation_failed, f"ID query failed\n{exc}")
            return
        self.app.call_from_thread(self._set_busy, False)
        self.app.call_from_thread(self._set_idn, f"ID: {identity.strip()}")
        self.app.call_from_thread(
            self._set_status, "Scope identity read successfully"
        )

    @work(thread=True)
    def _acquire_worker(self, channel: str) -> None:
        try:
            with self.devices.scope_for_operation() as scope:
                waveform = scope.acquire_waveform(channel)
        except Exception as exc:
            self.app.call_from_thread(
                self._operation_failed, f"Acquisition failed\n{exc}"
            )
            return
        self.app.call_from_thread(self._show_waveform, waveform)
        self.app.call_from_thread(self._set_busy, False)

    @work(thread=True)
    def _disconnect_worker(self) -> None:
        try:
            self.devices.close_scope()
        except Exception as exc:
            self.app.call_from_thread(
                self._operation_failed, f"Disconnect failed\n{exc}"
            )
            return
        self.app.call_from_thread(self._set_busy, False)
        self.app.call_from_thread(self._set_connection, "○ Disconnected")
        self.app.call_from_thread(self._set_idn, "ID: not queried")
        self.app.call_from_thread(self._set_status, "Scope disconnected")

    def _show_waveform(self, waveform: Waveform) -> None:
        self.query_one("#scope-plot", ScopeTracePlot).update_waveform(waveform)
        self._set_status(
            f"Acquired {waveform.channel}: {waveform.y.size:,} points\n"
            f"X: {waveform.x_unit}    Y: {waveform.y_unit}"
        )

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        button_id = event.button.id
        if button_id == "scope-connect":
            self._set_busy(True)
            self._set_status("Connecting to LeCroy…")
            self._connect_worker()
        elif button_id == "scope-idn-read":
            if not self.devices.scope_connected:
                self._set_status("Connect the scope first")
                return
            self._set_busy(True)
            self._set_status("Reading scope identity…")
            self._identify_worker()
        elif button_id == "scope-acquire":
            channel = self.query_one("#scope-channel", Input).value.strip() or "C1"
            if not self.devices.scope_connected:
                self._set_status("Connect the scope first")
                return
            self._set_busy(True)
            self._set_status(f"Acquiring {channel}…")
            self._acquire_worker(channel)
        elif button_id == "scope-disconnect":
            self._set_busy(True)
            self._disconnect_worker()
