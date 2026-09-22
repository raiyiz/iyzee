"""Scope screen: configure the LeCroy's channels and trigger, and plot
live waveforms.

Unlike the MXA/shutter/wavemeter, the scope driver (:class:`iyzee.scope.LeCroy`)
is write-only for most vertical/trigger settings — it has no ``*IDN?``-style
readback for volts/div, offset, or trigger level (see ``ScopeHandle``'s
docstring in ``instruments.py``). So this form doesn't try to read the
scope's current state on open; the fields start at sensible defaults
(matching ``SweepScreen``'s form, which does the same for its own
device-side parameters) and "Apply" only ever pushes settings outward.

"Acquire" is the read path: it pulls a waveform (``LeCroy.getDataFloats``)
plus that channel's time axis (``LeCroy.getHorProperties``) for every
enabled channel and draws them together on one plot, the same way
``SweepScreen``'s "Capture trace" and ``TracesScreen`` both funnel through
``plotting.draw_series``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Label, RichLog, Select, Static
from textual_plotext import PlotextPlot

from ...scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope
from ..plotting import draw_series
from ..text import one_line
from .page import Page

if TYPE_CHECKING:
    from ..app import IyzeeApp

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


class FieldError(ValueError):
    """A form field failed validation; ``field_id`` says which one.

    Same shape as ``SweepScreen``'s ``FieldError`` (a ``ValueError``
    subclass so callers that only care *that* the form is invalid keep
    working, while the screen can point at the offending field) — kept as
    its own class here, rather than imported, so this screen has no
    dependency on ``sweep.py``.
    """

    def __init__(self, field_id: str, message: str) -> None:
        super().__init__(message)
        self.field_id = field_id


def _field(label: str, widget: Widget, *, id: str | None = None) -> Vertical:
    """A label stacked over its input/select — one grid cell of a form.

    Same pattern as ``SweepScreen``'s private helper of the same name:
    grouping the pair lets ``app.tcss`` reflow the form as a grid without
    ever separating a label from the field it belongs to.
    """
    return Vertical(Label(label), widget, classes="field", id=id)


def _finite_float(raw: str, field: str) -> float:
    """Parse ``raw`` as a finite float.

    Unlike ``SweepScreen``'s ``_positive_float``, this allows zero and
    negative values — offsets and trigger levels are routinely negative.
    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    # float() happily parses "nan"/"inf"; neither is a usable setting.
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _positive_float(raw: str, field: str) -> float:
    value = _finite_float(raw, field)
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


@dataclass(frozen=True)
class _ChannelSettings:
    channel: Channel
    enabled: bool
    volts_per_div: float
    offset: float
    coupling: Coupling


@dataclass(frozen=True)
class _TriggerSettings:
    source: Channel
    mode: TriggerMode
    slope: TriggerSlope
    coupling: TriggerCoupling
    level_volts: float


def _channel_panel(channel: Channel) -> Vertical:
    color = CHANNEL_COLORS[channel]
    return Vertical(
        Static(f"[{color} b]{channel}[/{color} b]", classes="channel-title"),
        Checkbox("Show", value=channel == Channel.C1, id=f"{channel}-enable"),
        _field("V/div", Input(value="0.5", id=f"{channel}-vdiv")),
        _field("Offset (V)", Input(value="0.0", id=f"{channel}-offset")),
        _field(
            "Coupling",
            Select(
                COUPLING_CHOICES,
                value=Coupling.DC_1M.value,
                allow_blank=False,
                id=f"{channel}-coupling",
            ),
        ),
        classes="channel-panel",
        id=f"{channel}-panel",
    )


class ScopeScreen(Page):
    """Configure the scope's channels and trigger, then plot what it sees.

    Mirrors ``SweepScreen``'s overall shape (form -> controls -> plot ->
    log) but for the scope specifically: one panel per analog channel,
    one trigger section, an Apply button for each (they're independent
    commands on the instrument, so a mistake in one doesn't block the
    other), and an "Acquire" that downloads and plots a waveform per
    enabled channel.
    """

    @property
    def iyzee_app(self) -> IyzeeApp:
        """``self.app`` narrowed to the concrete app type (see ConnectScreen.iyzee_app)."""
        return cast("IyzeeApp", self.app)

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

    def on_input_changed(self, event: Input.Changed) -> None:
        # Editing a field you were told is wrong clears the marker.
        event.input.remove_class("-invalid")

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
            self.notify("Connect the scope first — press F1 for the Connect page.", severity="error")
            return None
        return handle.scope

    def _ui(self, callback, *args, **kwargs) -> None:
        """Call back into the UI thread from a worker, without letting a
        now-stale widget reference turn into an unhandled worker exception
        (see ``ConnectScreen._ui``).
        """
        try:
            self.app.call_from_thread(callback, *args, **kwargs)
        except Exception:
            log.exception("scope screen: UI update from worker thread failed")

    def _flag_invalid(self, field_id: str) -> None:
        """Mark one input as wrong and put the cursor in it, ready to fix."""
        for widget in self.query("Input.-invalid"):
            widget.remove_class("-invalid")
        widget = self.query_one(f"#{field_id}", Input)
        widget.add_class("-invalid")
        widget.focus()

    def _read(self, field_id: str, parse, label: str) -> float:
        """Parse one form field, tagging any failure with the field's id."""
        try:
            return parse(self.query_one(f"#{field_id}", Input).value, label)
        except ValueError as exc:
            raise FieldError(field_id, str(exc)) from exc

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-channels":
            self._start_apply_channels()
        elif event.button.id == "apply-trigger":
            self._start_apply_trigger()
        elif event.button.id == "acquire-waveforms":
            self._start_acquire()

    # -- channel settings ------------------------------------------------

    def _read_channel_settings(self) -> list[_ChannelSettings]:
        settings = []
        for channel in CHANNELS:
            vdiv = self._read(f"{channel}-vdiv", _positive_float, f"{channel} V/div")
            offset = self._read(f"{channel}-offset", _finite_float, f"{channel} offset")
            coupling = Coupling(self.query_one(f"#{channel}-coupling", Select).value)
            enabled = self.query_one(f"#{channel}-enable", Checkbox).value
            settings.append(_ChannelSettings(channel, enabled, vdiv, offset, coupling))
        return settings

    def _start_apply_channels(self) -> None:
        scope = self._scope()
        if scope is None:
            return
        try:
            settings = self._read_channel_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid channel settings: {exc}", severity="error", markup=False)
            return
        self.query_one("#apply-channels", Button).disabled = True
        self._apply_channels(scope, settings)

    @work(thread=True, exclusive=True, group="scope-apply-channels", exit_on_error=False)
    def _apply_channels(self, scope: LeCroy, settings: Sequence[_ChannelSettings]) -> None:
        errors: list[tuple[Channel, Exception]] = []
        with self.iyzee_app.instrument_locks["scope"]:
            for s in settings:
                try:
                    scope.set_volts_per_div(s.channel, s.volts_per_div)
                    scope.set_offset(s.channel, s.offset)
                    scope.set_coupling(s.channel, s.coupling)
                    scope.set_trace_display(s.channel, s.enabled)
                except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
                    log.exception("scope: failed to apply %s settings", s.channel)
                    errors.append((s.channel, exc))
        self._ui(self._finish_apply_channels, errors)

    def _finish_apply_channels(self, errors: Sequence[tuple[Channel, Exception]]) -> None:
        self.query_one("#apply-channels", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        if not errors:
            log_widget.write("Channel settings applied.")
            return
        for channel, exc in errors:
            log_widget.write(f"[red]{channel}: {escape(one_line(exc))}[/red]")
        self.notify("Some channel settings failed to apply — see the log.", severity="error")

    # -- trigger settings --------------------------------------------------

    def _read_trigger_settings(self) -> _TriggerSettings:
        level = self._read("trig-level", _finite_float, "Trigger level")
        source = Channel(self.query_one("#trig-source", Select).value)
        mode = TriggerMode(self.query_one("#trig-mode", Select).value)
        slope = TriggerSlope(self.query_one("#trig-slope", Select).value)
        coupling = TriggerCoupling(self.query_one("#trig-coupling", Select).value)
        return _TriggerSettings(source, mode, slope, coupling, level)

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
    def _apply_trigger(self, scope: LeCroy, settings: _TriggerSettings) -> None:
        error: Exception | None = None
        with self.iyzee_app.instrument_locks["scope"]:
            try:
                scope.set_trigger_mode(settings.mode)
                scope.set_trigger_source(settings.source)
                scope.set_trigger_slope(settings.source, settings.slope)
                scope.set_trigger_coupling(settings.source, settings.coupling)
                scope.set_trigger_level(settings.source, settings.level_volts)
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
        series: list[tuple[list[float], list[float], str]] = []
        errors: list[tuple[Channel, Exception]] = []
        with self.iyzee_app.instrument_locks["scope"]:
            for channel in channels:
                try:
                    _unit, values = scope.getDataFloats(channel=channel)
                    _hor_unit, hor_offset, hor_interval = scope.getHorProperties(channel=channel)
                    times = [hor_offset + i * hor_interval for i in range(len(values))]
                    series.append((times, list(values), str(channel)))
                except Exception as exc:  # noqa: BLE001
                    log.exception("scope: failed to acquire %s", channel)
                    errors.append((channel, exc))
        self._ui(self._finish_acquire, series, errors)

    def _finish_acquire(
        self,
        series: Sequence[tuple[list[float], list[float], str]],
        errors: Sequence[tuple[Channel, Exception]],
    ) -> None:
        self.query_one("#acquire-waveforms", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        for channel, exc in errors:
            log_widget.write(f"[red]{channel}: {escape(one_line(exc))}[/red]")
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
