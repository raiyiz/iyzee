"""Dedicated Textual screen for LeCroy scope control."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static

from ..scope import Waveform
from .devices import DeviceManager
from .scope_plot import ScopeTracePlot


class ScopeScreen(Screen[None]):
    """Connect, inspect, and acquire a waveform from the LeCroy scope."""

    BINDINGS = [("escape", "close", "Back")]

    CSS = """
    Screen {
        layout: vertical;
        padding: 1 2;
    }

    #scope-body {
        height: 1fr;
    }

    #scope-controls {
        width: 34;
        padding: 1;
        border: round $primary;
    }

    #scope-main {
        width: 1fr;
        padding-left: 2;
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

    #scope-status {
        height: 5;
        border: round $secondary;
        padding: 1;
        margin-bottom: 1;
    }

    #scope-plot {
        height: 1fr;
        min-height: 20;
        border: round $primary;
    }

    .muted {
        color: $text-muted;
    }
    """

    def __init__(self, devices: DeviceManager) -> None:
        super().__init__()
        self.devices = devices
        self._busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="scope-body"):
            with Vertical(id="scope-controls"):
                yield Label("LECROY SCOPE", classes="section-title")
                yield Static("○ Disconnected", id="scope-connection")
                with Horizontal(classes="control-row"):
                    yield Button("Connect", id="scope-connect", variant="primary")
                    yield Button("Disconnect", id="scope-disconnect")

                yield Label("Acquisition", classes="section-title")
                with Horizontal(classes="control-row"):
                    yield Input("C1", id="scope-channel", placeholder="C1")
                    yield Button("Acquire", id="scope-acquire", variant="success")

                yield Static(
                    "Acquires one waveform using the scope's current front-panel configuration.",
                    classes="muted",
                )

            with Vertical(id="scope-main"):
                yield Static("Ready — scope not connected", id="scope-status")
                yield ScopeTracePlot(id="scope-plot")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget_id in ("scope-connect", "scope-disconnect", "scope-acquire", "scope-channel"):
            self.query_one(f"#{widget_id}").disabled = busy

    def _status(self, text: str) -> None:
        self.query_one("#scope-status", Static).update(text)

    @work(thread=True)
    def _connect_worker(self) -> None:
        try:
            self.devices.connect_scope()
        except Exception as exc:
            self.call_from_thread(self._set_busy, False)
            self.call_from_thread(self._status, f"Connection failed\n{exc}")
            self.call_from_thread(self.query_one("#scope-connection", Static).update, "× Connection failed")
            return
        self.call_from_thread(self._set_busy, False)
        self.call_from_thread(self.query_one("#scope-connection", Static).update, "● Connected")
        self.call_from_thread(self._status, "Scope connected — ready to acquire")

    @work(thread=True)
    def _acquire_worker(self, channel: str) -> None:
        try:
            with self.devices.scope_for_operation() as scope:
                waveform = scope.acquire_waveform(channel)
        except Exception as exc:
            self.call_from_thread(self._set_busy, False)
            self.call_from_thread(self._status, f"Acquisition failed\n{exc}")
            return

        self.call_from_thread(self._show_waveform, waveform)
        self.call_from_thread(self._set_busy, False)

    @work(thread=True)
    def _disconnect_worker(self) -> None:
        try:
            self.devices.close_scope()
        except Exception as exc:
            self.call_from_thread(self._set_busy, False)
            self.call_from_thread(self._status, f"Disconnect failed\n{exc}")
            return
        self.call_from_thread(self._set_busy, False)
        self.call_from_thread(self.query_one("#scope-connection", Static).update, "○ Disconnected")
        self.call_from_thread(self._status, "Scope disconnected")

    def _show_waveform(self, waveform: Waveform) -> None:
        self.query_one("#scope-plot", ScopeTracePlot).update_waveform(waveform)
        self._status(
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
            self._status("Connecting to LeCroy…")
            self._connect_worker()
        elif button_id == "scope-acquire":
            channel = self.query_one("#scope-channel", Input).value.strip() or "C1"
            if not self.devices.scope_connected:
                self._status("Connect the scope first")
                return
            self._set_busy(True)
            self._status(f"Acquiring {channel}…")
            self._acquire_worker(channel)
        elif button_id == "scope-disconnect":
            self._set_busy(True)
            self._disconnect_worker()

    def action_close(self) -> None:
        self.app.pop_screen()
