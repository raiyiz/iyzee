"""Power page: the supply's four channels and the shutter, read back and switchable.

The shutter is not a separate instrument: it is PSU channel 3 (see
:class:`~iyzee.devices.power.ShutterControl`), and the shutter's connection is the one
link to the supply this app holds. So the page works whenever the **Shutter** is connected,
and talks to the supply through it, under that handle's lock. A second socket to the same
supply would race over which channel is selected.

Everything shown is what the supply reports (``PSU.retrieve_state``), never what we last
asked for. After every change the page reads the state back, so a value the supply
rounded, clamped or refused is displayed as it really is.

Reading while a sweep runs is skipped: the run holds the shutter's lock for its whole
duration, and a manual change in the middle of it would corrupt the measurement.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Button, Input, Label, RichLog, Static

from ...devices.handles import InstrumentHandle
from ...devices.power import CHANNELS, CURRENT_MAX, VOLTAGE_MAX, PSUState, ShutterControl
from ..commands import Command
from ..text import one_line
from .page import FieldError, Page, _field, _finite_float

log = logging.getLogger("iyzee.tui")

#: Seconds between background reads while the page is shown and idle.
POLL_INTERVAL_S = 2.0

#: A change to make, run with the handle's lock held; the state is read back afterwards.
Action = Callable[[ShutterControl], None]


def _limited_float(raw: str, field: str, *, maximum: float) -> float:
    """A finite number from 0 to ``maximum`` (what the supply accepts for a setpoint)."""
    value = _finite_float(raw, field)
    if not 0.0 <= value <= maximum:
        raise ValueError(f"{field} must be between 0 and {maximum:g}")
    return value


def _fmt(value: float, unit: str) -> str:
    return f"{value:.3f} {unit}" if math.isfinite(value) else f"- {unit}"


class PowerScreen(Page):
    """Master output, four channel panels (setpoints, readings, switches) and the shutter."""

    def compose(self) -> ComposeResult:
        yield Static("Power supply & shutter", classes="panel-title")
        # Shown only while the shutter (and with it the supply) isn't connected.
        yield Static("", id="power-status")
        yield Static("Not read yet", id="power-summary")
        with Horizontal(id="power-controls"):
            yield Button("Global output", id="power-global")
            yield Button("Shutter", id="power-shutter")
            yield Button("Refresh", id="power-refresh", variant="primary")
        with Grid(id="power-channels"):
            for number in CHANNELS:
                with Vertical(id=f"power-ch{number}", classes="channel-panel"):
                    yield Static(f"CH{number}", id=f"power-title{number}", classes="channel-title")
                    yield Static("-", id=f"power-readout{number}")
                    yield _field("Voltage (V)", Input(id=f"power-v{number}", placeholder="0"))
                    yield _field("Current limit (A)", Input(id=f"power-i{number}", placeholder="0"))
                    yield Button("Apply V / I", id=f"power-apply{number}")
                    yield Button("Channel", id=f"power-toggle{number}")
        yield Label("Log", classes="hint")
        yield RichLog(id="power-log", wrap=True, markup=True)

    def on_mount(self) -> None:
        self._busy = False
        self._synced = False  # the inputs have been filled from the supply for this connection
        self._state: PSUState | None = None
        self.set_interval(POLL_INTERVAL_S, self._poll)
        self._refresh_ui()

    def on_show(self) -> None:
        self.refresh_readiness()

    # -- connection ----------------------------------------------------------------------

    def _handle(self) -> InstrumentHandle | None:
        return self.iyzee_app.handles.get("shutter")

    def _shutter(self) -> ShutterControl | None:
        handle = self._handle()
        device = handle.device if handle is not None else None
        return device if isinstance(device, ShutterControl) else None

    def _is_current(self, handle: InstrumentHandle) -> bool:
        return self._handle() is handle

    def _sweep_running(self) -> bool:
        return self.iyzee_app.sweep_running

    def refresh_readiness(self) -> None:
        """Follow the shutter's connection: read the supply once on connect, clear on loss."""
        if self._shutter() is None:
            self._state = None
            self._synced = False
            self._busy = False
        elif not self._synced and not self._busy and not self._sweep_running():
            self._start(None, "read the supply", silent=True, fill_inputs=True)
        self._refresh_ui()

    # -- display -------------------------------------------------------------------------

    def _channel_status(self, state: PSUState, number: int) -> str:
        channel = state.channel(number)
        if state.is_live(number):
            return "[green]ON[/green]"
        if channel.selected:
            return "[yellow]armed[/yellow] (master output is off)"
        return "off"

    def _refresh_ui(self) -> None:
        connected = self._shutter() is not None
        status = self.query_one("#power-status", Static)
        status.update(
            "Not ready: connect the Shutter first (it holds the supply's link) - press F1."
        )
        status.display = not connected

        state = self._state
        enabled = connected and not self._busy and not self._sweep_running()
        summary = self.query_one("#power-summary", Static)
        shutter = self._shutter()
        glob = self.query_one("#power-global", Button)
        shutter_button = self.query_one("#power-shutter", Button)

        if state is None:
            summary.update("Talking to the supply…" if self._busy else "Not read yet")
            glob.label = "Global output"
            shutter_button.label = "Shutter"
        else:
            master = "[green]ON[/green]" if state.general_output else "off"
            text = f"Global output: {master}"
            if shutter is not None:
                chan = shutter.chan
                if state.is_live(chan):
                    opened = "[green]OPEN[/green]"
                elif state.channel(chan).selected:
                    opened = "[yellow]armed[/yellow], closed until global output is on"
                else:
                    opened = "closed"
                text += f"   Shutter (CH{chan}): {opened}"
                shutter_button.label = "Close shutter" if state.is_live(chan) else "Open shutter"
            if self._busy:
                text += "   [dim]working…[/dim]"
            if self._sweep_running():
                text += "   [dim]a sweep is running: controls are locked[/dim]"
            summary.update(text)
            glob.label = (
                "Turn global output OFF" if state.general_output else "Turn global output ON"
            )
            glob.variant = "success" if state.general_output else "default"

        glob.disabled = not enabled or state is None
        shutter_button.disabled = not enabled or state is None or shutter is None
        self.query_one("#power-refresh", Button).disabled = not enabled

        for number in CHANNELS:
            title = f"CH{number}"
            if shutter is not None and number == shutter.chan:
                title += "  [dim](shutter)[/dim]"
            self.query_one(f"#power-title{number}", Static).update(title)
            readout = self.query_one(f"#power-readout{number}", Static)
            toggle = self.query_one(f"#power-toggle{number}", Button)
            if state is None:
                readout.update("-")
                toggle.label = f"Channel {number}"
            else:
                ch = state.channel(number)
                readout.update(
                    f"{self._channel_status(state, number)}\n"
                    f"set  {_fmt(ch.voltage_set, 'V')} / {_fmt(ch.current_set, 'A')}\n"
                    f"read {_fmt(ch.voltage_measured, 'V')} / {_fmt(ch.current_measured, 'A')}"
                )
                toggle.label = f"Disable CH{number}" if ch.selected else f"Enable CH{number}"
                toggle.variant = "success" if ch.selected else "default"
            toggle.disabled = not enabled or state is None
            self.query_one(f"#power-apply{number}", Button).disabled = not enabled or state is None

    def _fill_inputs(self, state: PSUState, channels: tuple[int, ...] = CHANNELS) -> None:
        for number in channels:
            ch = state.channel(number)
            self.query_one(f"#power-v{number}", Input).value = f"{ch.voltage_set:g}"
            self.query_one(f"#power-i{number}", Input).value = f"{ch.current_set:g}"

    def _write_log(self, message: str, *, error: bool = False) -> None:
        self.query_one("#power-log", RichLog).write(f"[red]{message}[/red]" if error else message)

    # -- talking to the supply -----------------------------------------------------------

    def _start(
        self,
        action: Action | None,
        what: str,
        *,
        silent: bool = False,
        fill_inputs: bool = False,
        fill_channels: tuple[int, ...] = (),
    ) -> None:
        handle, shutter = self._handle(), self._shutter()
        if handle is None or shutter is None or self._busy:
            return
        self._busy = True
        self._refresh_ui()
        self._operate(handle, shutter, action, what, silent, fill_inputs, fill_channels)

    @work(thread=True, group="power", exit_on_error=False)
    def _operate(
        self,
        handle: InstrumentHandle,
        shutter: ShutterControl,
        action: Action | None,
        what: str,
        silent: bool,
        fill_inputs: bool,
        fill_channels: tuple[int, ...],
    ) -> None:
        state: PSUState | None = None
        error: Exception | None = None
        try:
            with handle.lock:
                try:
                    if action is not None:
                        action(shutter)
                except Exception as exc:  # noqa: BLE001 - still read back what the supply is doing
                    log.exception("power: %s failed", what)
                    error = exc
                # Even after a failed change: show the supply's real state, not a guess.
                try:
                    state = shutter.psu.retrieve_state()
                except Exception as exc:  # noqa: BLE001 - never leave the page busy
                    log.exception("power: reading the supply failed")
                    error = error or exc
        except Exception as exc:  # noqa: BLE001 - e.g. the lock itself
            error = error or exc
        self._ui(self._finish, handle, what, state, error, silent, fill_inputs, fill_channels)

    def _finish(
        self,
        handle: InstrumentHandle,
        what: str,
        state: PSUState | None,
        error: Exception | None,
        silent: bool,
        fill_inputs: bool,
        fill_channels: tuple[int, ...],
    ) -> None:
        self._busy = False
        if not self._is_current(handle):  # disconnected (or replaced) while we were talking
            self._refresh_ui()
            return
        if state is not None:
            self._state = state
            if fill_inputs:
                self._fill_inputs(state)
                self._synced = True
            elif fill_channels:
                self._fill_inputs(state, fill_channels)
        if error is not None:
            self._write_log(f"Could not {what}: {escape(one_line(error))}", error=True)
            if not silent:
                self.notify(f"Could not {what} - see the log.", severity="warning", timeout=6)
        elif not silent:
            self._write_log(f"Done: {what}.")
        self._refresh_ui()

    def _poll(self) -> None:
        """Keep the readings live while the page is shown, idle and nothing else is using the link."""
        if (
            self.display
            and self._shutter() is not None
            and self._synced
            and not self._busy
            and not self._sweep_running()
        ):
            self._start(None, "read the supply", silent=True)

    # -- buttons -------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "power-refresh":
            self._start(None, "read the supply", fill_inputs=True)
        elif button_id == "power-global":
            self._toggle_global()
        elif button_id == "power-shutter":
            self._toggle_shutter()
        elif button_id.startswith("power-toggle"):
            self._toggle_channel(int(button_id.removeprefix("power-toggle")))
        elif button_id.startswith("power-apply"):
            self._apply_channel(int(button_id.removeprefix("power-apply")))
        else:
            return
        event.stop()

    def _guard(self) -> bool:
        """Whether a change may be sent now (said so, if not)."""
        if self._sweep_running():
            self.notify("A sweep is running: wait for it to finish.", severity="warning")
            return False
        return self._state is not None and not self._busy

    def _toggle_global(self) -> None:
        if not self._guard() or self._state is None:
            return
        turn_on = not self._state.general_output
        self._start(
            (lambda s: s.psu.enable_global_output())
            if turn_on
            else (lambda s: s.psu.disable_global_output()),
            f"turn global output {'on' if turn_on else 'off'}",
        )

    def _toggle_shutter(self) -> None:
        shutter = self._shutter()
        if not self._guard() or self._state is None or shutter is None:
            return
        opening = not self._state.is_live(shutter.chan)
        self._start(
            (lambda s: s.open()) if opening else (lambda s: s.close()),
            f"{'open' if opening else 'close'} the shutter",
        )

    def _toggle_channel(self, number: int) -> None:
        if not self._guard() or self._state is None:
            return
        enable = not self._state.channel(number).selected
        self._start(
            (lambda s: s.psu.enable_output(number))
            if enable
            else (lambda s: s.psu.disable_output(number)),
            f"{'enable' if enable else 'disable'} CH{number}",
        )

    def _apply_channel(self, number: int) -> None:
        if not self._guard():
            return
        try:
            voltage = self._read(f"power-v{number}", _limited_float, "Voltage", maximum=VOLTAGE_MAX)
            current = self._read(
                f"power-i{number}", _limited_float, "Current limit", maximum=CURRENT_MAX
            )
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(str(exc), severity="error", timeout=5, markup=False)
            return

        def apply(shutter: ShutterControl) -> None:
            shutter.psu.set_voltage(voltage, number)
            shutter.psu.set_current(current, number)

        self._start(
            apply,
            f"set CH{number} to {voltage:g} V / {current:g} A",
            fill_channels=(number,),
        )

    # -- commands --------------------------------------------------------------------------

    def commands(self) -> list[Command]:
        return [
            Command(
                "refresh",
                lambda _: self.press_button("power-refresh", "read the supply"),
                "Read the supply's state into the page",
            ),
        ]


__all__ = ["PowerScreen"]
