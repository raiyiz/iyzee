"""Scope screen: configure the LeCroy's channels and trigger, and plot
live waveforms.

The actual operations ("push these channel settings", "acquire every
enabled channel") live in ``iyzee.scope_workflows``, not here — this
screen's job is the form (reading/validating ``Input``/``Select`` widgets
into a ``ChannelSettings``/``TriggerSettings``), the buttons, the plot,
and reporting the result. See ``scope_workflows``'s module docstring for
why: the same operations this screen's buttons trigger are meant to be
identically usable from a script or the IPython console, which they
cannot be if the logic lives on a Textual widget.

Unlike the MXA/shutter/wavemeter, the scope driver (:class:`iyzee.scope.LeCroy`)
is write-only for most vertical/trigger settings — it has no ``*IDN?``-style
readback for volts/div, offset, or trigger level (see ``ScopeHandle``'s
docstring in ``instruments.py``). So this form doesn't try to read the
scope's current state on open; the fields start at sensible defaults
(matching ``SweepScreen``'s form, which does the same for its own
device-side parameters) and "Apply" only ever pushes settings outward.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static
from textual_plotext import PlotextPlot

from ...scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope
from ...scope_workflows import (
    ChannelError,
    ChannelSettings,
    TriggerSettings,
    acquire_waveforms,
    apply_channel_settings,
    apply_trigger_settings,
)
from ..plotting import draw_series
from ..text import one_line
from .page import FieldError, Page, _field

log = logging.getLogger("iyzee.tui")

CHANNELS: tuple[Channel, ...] = (Channel.C1, Channel.C2, Channel.C3, Channel.C4)

# Matches the trace colours the instrument itself uses for C1-C4, so the
# channel panel you're editing and the line it produces on "Acquire" read
# as the same channel at a glance, without relying on a shared legend.
CHANNEL_COLORS: dict[Channel, str] = {
    Channel.C1: "yellow",
    Channel.C2: "green",
    Channel.C3: "magenta",
    Channel.C4: "cyan",
}

COUPLING_CHOICES = [
    ("50\u03a9 DC", Coupling.DC_50.value),
    ("1M\u03a9 DC", Coupling.DC_1M.value),
    ("1M\u03a9 AC", Coupling.AC_1M.value),
    ("Ground", Coupling.GROUND.value),
]

TRIGGER_SOURCE_CHOICES = [(str(channel), channel.value) for channel in CHANNELS]

TRIGGER_MODE_CHOICES = [
    ("Auto", TriggerMode.AUTO.value),
    ("Normal", TriggerMode.NORMAL.value),
    ("Single", TriggerMode.SINGLE.value),
    ("Stop", TriggerMode.STOP.value),
]

TRIGGER_SLOPE_CHOICES = [
    ("Rising", TriggerSlope.POSITIVE.value),
    ("Falling", TriggerSlope.NEGATIVE.value),
]

TRIGGER_COUPLING_CHOICES = [
    ("DC", TriggerCoupling.DC.value),
    ("AC", TriggerCoupling.AC.value),
    ("HF reject", TriggerCoupling.HF_REJECT.value),
    ("LF reject", TriggerCoupling.LF_REJECT.value),
]


class ScopeScreen(Page):
    """Configure the scope's channels and trigger, then plot what it sees.

    Mirrors ``SweepScreen``'s overall shape (form -> controls -> plot ->
    log) but for the scope specifically: one panel per analog channel,
    one trigger section, an Apply button for each (they're independent
    commands on the instrument, so a mistake in one doesn't block the
    other), and an "Acquire" that downloads and plots a waveform per
    enabled channel.
    """


    def compose(self) -> ComposeResult:
        yield Static("Scope", classes="panel-title")
        # Shown only while the scope isn't connected; see refresh_readiness().
        yield Static("", id="scope-status")
        with Grid(id="scope-channels"):
            for channel in CHANNELS:
                yield _channel_panel(channel)
        yield Button("Apply channel settings", id="apply-channels")
        with Vertical(id="scope-trigger"):
            yield Static("Trigger", classes="panel-title")
            with Grid(id="trigger-fields", classes="field-grid"):
                yield _field(
                    "Source",
                    Select(
                        TRIGGER_SOURCE_CHOICES,
                        value=Channel.C1.value,
                        allow_blank=False,
                        id="trig-source",
                    ),
                )
                yield _field(
                    "Mode",
                    Select(
                        TRIGGER_MODE_CHOICES,
                        value=TriggerMode.AUTO.value,
                        allow_blank=False,
                        id="trig-mode",
                    ),
                )
                yield _field(
                    "Slope",
                    Select(
                        TRIGGER_SLOPE_CHOICES,
                        value=TriggerSlope.POSITIVE.value,
                        allow_blank=False,
                        id="trig-slope",
                    ),
                )
                yield _field(
                    "Coupling",
                    Select(
                        TRIGGER_COUPLING_CHOICES,
                        value=TriggerCoupling.DC.value,
                        allow_blank=False,
                        id="trig-coupling",
                    ),
                )
                yield _field("Level (V)", Input(value="0.0", id="trig-level"))
            yield Button("Apply trigger", id="apply-trigger")
        with Grid(id="scope-controls"):
            yield Button("Acquire", id="acquire-waveforms", variant="success")
        yield PlotextPlot(id="scope-plot")
        yield RichLog(id="scope-log", highlight=False, markup=True)

    def on_mount(self) -> None:
        plot = self.query_one("#scope-plot", PlotextPlot)
        plot.plt.title("Scope waveforms")
        plot.plt.xlabel("Time (s)")
        plot.plt.ylabel("Voltage (V)")
        self.refresh_readiness()

    def on_show(self) -> None:
        self.refresh_readiness()


    # -- readiness -----------------------------------------------------------

    def refresh_readiness(self) -> None:
        """Show, at the top of the page, why nothing here will work yet.

        Same pattern as ``SweepScreen.refresh_readiness``: the buttons stay
        enabled so pressing one still explains what's missing, rather than
        a disabled button that gives no way to ask "why?".
        """
        status = self.query_one("#scope-status", Static)
        connected = "scope" in self.iyzee_app.handles
        if not connected:
            status.update("Not ready: connect the Scope first — press F1 for the Connect page.")
        status.display = not connected

    def _scope(self) -> LeCroy | None:
        """The live driver, or ``None`` (after notifying) if not connected."""
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self.notify(
                "Connect the scope first — press F1 for the Connect page.", severity="error"
            )
            return None
        return handle.scope


    @work(thread=True, exclusive=True, group="scope-apply-channels", exit_on_error=False)
    def _apply_channels(self, scope: LeCroy, settings: Sequence[ChannelSettings]) -> None:
        errors = apply_channel_settings(scope, settings, lock=self.iyzee_app.handles["scope"].lock)
        self._ui(self._finish_apply_channels, errors)

    def _finish_apply_channels(self, errors: Sequence[ChannelError]) -> None:
        self.query_one("#apply-channels", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        if not errors:
            log_widget.write("Channel settings applied.")
            return
        for err in errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
        self.notify("Some channel settings failed to apply — see the log.", severity="error")

    # -- trigger settings --------------------------------------------------

    def _read_trigger_settings(self) -> TriggerSettings:
        level = self._read("trig-level", _finite_float, "Trigger level")
        source = Channel(self.query_one("#trig-source", Select).value)
        mode = TriggerMode(self.query_one("#trig-mode", Select).value)
        slope = TriggerSlope(self.query_one("#trig-slope", Select).value)
        coupling = TriggerCoupling(self.query_one("#trig-coupling", Select).value)
        return TriggerSettings(source, mode, slope, coupling, level)

    def _start_apply_trigger(self) -> None:
        scope = self._scope()
        if scope is None:
            return
        try:
            settings = self._read_trigger_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid trigger settings: {exc}", severity="error", markup=False)
            return
        self.query_one("#apply-trigger", Button).disabled = True
        self._apply_trigger(scope, settings)

    @work(thread=True, exclusive=True, group="scope-apply-trigger", exit_on_error=False)
    def _apply_trigger(self, scope: LeCroy, settings: TriggerSettings) -> None:
        error: Exception | None = None
        try:
            apply_trigger_settings(scope, settings, lock=self.iyzee_app.handles["scope"].lock)
        except Exception as exc:  # noqa: BLE001
            log.exception("scope: failed to apply trigger settings")
            error = exc
        self._ui(self._finish_apply_trigger, error)

    def _finish_apply_trigger(self, error: Exception | None) -> None:
        self.query_one("#apply-trigger", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        if error is None:
            log_widget.write("Trigger settings applied.")
            return
        log_widget.write(f"[red]Trigger: {escape(one_line(error))}[/red]")
        self.notify(f"Trigger settings failed: {one_line(error)}", severity="error", markup=False)

    # -- acquire: download and plot a waveform per enabled channel ---------

    def _start_acquire(self) -> None:
        scope = self._scope()
        if scope is None:
            return
        channels = [
            channel for channel in CHANNELS if self.query_one(f"#{channel}-enable", Checkbox).value
        ]
        if not channels:
            self.notify("Enable at least one channel first.", severity="warning")
            return
        self.query_one("#acquire-waveforms", Button).disabled = True
        log_widget = self.query_one("#scope-log", RichLog)
        log_widget.write(f"Acquiring {', '.join(str(c) for c in channels)}…")
        self._acquire(scope, channels)

    @work(thread=True, exclusive=True, group="scope-acquire", exit_on_error=False)
    def _acquire(self, scope: LeCroy, channels: Sequence[Channel]) -> None:
        series, errors = acquire_waveforms(
            scope, channels, lock=self.iyzee_app.handles["scope"].lock
        )
        self._ui(self._finish_acquire, series, errors)

    def _finish_acquire(
        self,
        series: Sequence[tuple[list[float], list[float], str]],
        errors: Sequence[ChannelError],
    ) -> None:
        self.query_one("#acquire-waveforms", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        for err in errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
        if series:
            plot = self.query_one("#scope-plot", PlotextPlot)
            draw_series(
                plot,
                series,
                title="Scope waveforms",
                xlabel="Time (s)",
                ylabel="Voltage (V)",
            )
            log_widget.write(f"Acquired {len(series)} channel(s).")
        elif errors:
            self.notify("Acquisition failed — see the log.", severity="error")
