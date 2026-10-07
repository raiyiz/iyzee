"""Scope screen: configure the LeCroy's channels and trigger, and plot
live waveforms.

The actual operations ("push these channel settings", "read back what's
on the scope now", "acquire every enabled channel") live in
``iyzee.scope_workflows``, not here — this screen's job is the form
(reading/validating ``Input``/``Select`` widgets into a
``ChannelSettings``/``TriggerSettings``, or writing one back into those
same widgets), the buttons, the plot, and reporting the result. See
``scope_workflows``'s module docstring for why: the same operations this
screen's buttons trigger are meant to be identically usable from a script
or the IPython console, which they cannot be if the logic lives on a
Textual widget.

Three buttons, three background operations. *Retrieve* reads the scope into
the form (it also runs once by itself when the scope is connected). *Apply*
writes whatever differs from what the scope last reported — channels and
trigger together — then refreshes the form from the read-back, so a value the
scope rounded or ignored shows up as what it really is. *Acquire* records the
enabled channels. Apply and Acquire need a successful Retrieve first, so
defaults in the form are never pushed to an instrument that hasn't been read.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static
from textual_plotext import PlotextPlot

from ...devices.scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope
from ...experiment import create_dirs
from ...scope_workflows import (
    ChannelSettings,
    ScopeAcquisition,
    TriggerSettings,
    acquire_scope_recording,
    apply_channel_settings,
    apply_trigger_settings,
    read_channel_settings,
    read_trigger_settings,
    save_scope_acquisition,
)
from ..commands import Command, CommandError, parse_si
from ..plotting import describe_timebase, draw_series, format_si, time_series
from ..text import one_line
from .page import FieldError, Page, _field, _finite_float, _positive_float

if TYPE_CHECKING:
    from ..instruments import InstrumentHandle

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
    """Configure the scope, record enabled channels, then plot the result.

    Mirrors ``SweepScreen``'s overall shape (form -> controls -> plot ->
    log) but for the scope specifically: one panel per analog channel,
    one trigger section, one Apply button, and an "Acquire & save"
    action that records the selected waveforms before plotting them.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Set here, not in on_mount: refresh_readiness() can run before the page is mounted.
        # What the scope itself last reported (None until a successful Retrieve).
        self._channel_state: tuple[ChannelSettings, ...] | None = None
        self._trigger_state: TriggerSettings | None = None
        self._busy = False

    @property
    def _synced(self) -> bool:
        return self._channel_state is not None and self._trigger_state is not None

    def compose(self) -> ComposeResult:
        yield Static("Scope", classes="panel-title")
        # Shown only while the scope isn't connected; see refresh_readiness().
        yield Static("", id="scope-status")
        yield Static("Not synchronized", id="scope-sync-status", classes="scope-sync-status")
        yield Button("Retrieve current settings", id="retrieve-settings", variant="primary")
        with Grid(id="scope-channels"):
            for channel in CHANNELS:
                yield _channel_panel(channel)
        with Vertical(id="scope-trigger"):
            yield Static("Trigger & timebase", classes="panel-title")
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
                yield _field("Time/div (s)", Input(value="1e-6", id="trig-tdiv"))
        yield Button("Apply changes", id="apply-settings")
        with Grid(id="scope-controls"):
            yield Button("Acquire & save", id="acquire-waveforms", variant="success")
        yield PlotextPlot(id="scope-plot")
        yield RichLog(id="scope-log", highlight=False, markup=True)

    def on_mount(self) -> None:
        plot = self.query_one("#scope-plot", PlotextPlot)
        plot.plt.title("Scope waveforms")
        plot.plt.xlabel("Time (s)")
        plot.plt.ylabel("Voltage (V)")
        self.refresh_readiness()
        self._refresh_scope_ui()

    def on_show(self) -> None:
        self.refresh_readiness()

    # -- state ---------------------------------------------------------------

    def _refresh_scope_ui(self) -> None:
        """Update the status text and which actions can run."""
        status = self.query_one("#scope-sync-status", Static)
        if self._busy:
            status.update("Talking to the scope…")
        elif self._synced:
            status.update("Scope settings read from the instrument.")
        else:
            status.update("Scope settings not read yet — press Retrieve.")
        ready = self._synced and not self._busy
        self.query_one("#retrieve-settings", Button).disabled = self._busy
        self.query_one("#apply-settings", Button).disabled = not ready
        self.query_one("#acquire-waveforms", Button).disabled = not ready

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._refresh_scope_ui()

    # -- readiness -----------------------------------------------------------

    def refresh_readiness(self) -> None:
        """Keep connection readiness and scope-state synchronization current.

        A newly connected scope is read automatically once so the form starts
        from the instrument's real state instead of its UI defaults. The
        explicit *Retrieve current settings* button remains useful after a
        front-panel change.
        """
        status = self.query_one("#scope-status", Static)
        connected = "scope" in self.iyzee_app.handles
        if not connected:
            self._channel_state = None
            self._trigger_state = None
            self._busy = False
            self._refresh_scope_ui()
            status.update("Not ready: connect the Scope first — press F1 for the Connect page.")
        elif not self._synced and not self._busy:
            self._start_retrieve(silent=True)
        status.display = not connected

    def _scope_is_current(self, scope: LeCroy) -> bool:
        """Return whether the scope object is still the connected instance."""
        handle = self.iyzee_app.handles.get("scope")
        return handle is not None and handle.device is scope

    def _scope_handle(self) -> InstrumentHandle | None:
        """The connected scope's handle, or ``None`` (after notifying) if not connected."""
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self.notify(
                "Connect the scope first — press F1 for the Connect page.", severity="error"
            )
        return handle

    def commands(self) -> list[Command]:
        def tdiv(args: list[str]) -> str:
            if len(args) != 1:
                raise CommandError("usage: :tdiv <seconds/div>, e.g. :tdiv 2u")
            seconds = parse_si(args[0])
            if seconds <= 0:
                raise CommandError("time/div must be positive")
            self.query_one("#trig-tdiv", Input).value = repr(seconds)
            return self.press_button("apply-settings", f"set time/div to {format_si(seconds, 's')}")

        return [
            Command(
                "retrieve",
                lambda _: self.press_button("retrieve-settings", "retrieve settings"),
                "Read the scope's current settings into the form",
            ),
            Command(
                "apply",
                lambda _: self.press_button("apply-settings", "apply changes"),
                "Send changed channel/trigger settings to the scope",
            ),
            Command(
                "acquire",
                lambda _: self.press_button("acquire-waveforms", "acquire waveforms"),
                "Acquire waveforms from the enabled channels",
            ),
            Command(
                "tdiv",
                tdiv,
                "Set time/div and apply it (2u, 500n, 1.5m, 1e-6)",
                usage="<seconds/div>",
            ),
        ]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "retrieve-settings":
            self._start_retrieve()
        elif event.button.id == "apply-settings":
            self._start_apply()
        elif event.button.id == "acquire-waveforms":
            self._start_acquire()

    # -- showing scope state in the form --------------------------------------

    def _show_channel(self, settings: ChannelSettings) -> None:
        """Write one channel's values into its form fields."""
        channel = settings.channel
        self.query_one(f"#{channel}-enable", Checkbox).value = settings.enabled
        self.query_one(f"#{channel}-vdiv", Input).value = str(settings.volts_per_div)
        self.query_one(f"#{channel}-offset", Input).value = str(settings.offset)
        self.query_one(f"#{channel}-coupling", Select).value = settings.coupling.value
        for field_id in (f"{channel}-vdiv", f"{channel}-offset"):
            self.query_one(f"#{field_id}", Input).remove_class("-invalid")

    def _show_trigger(self, settings: TriggerSettings) -> None:
        self.query_one("#trig-source", Select).value = settings.source.value
        self.query_one("#trig-mode", Select).value = settings.mode.value
        self.query_one("#trig-slope", Select).value = settings.slope.value
        self.query_one("#trig-coupling", Select).value = settings.coupling.value
        self.query_one("#trig-level", Input).value = str(settings.level_volts)
        self.query_one("#trig-level", Input).remove_class("-invalid")
        if settings.time_per_div is not None:
            self.query_one("#trig-tdiv", Input).value = str(settings.time_per_div)
            self.query_one("#trig-tdiv", Input).remove_class("-invalid")

    def _read_channel_settings(self) -> list[ChannelSettings]:
        settings = []
        for channel in CHANNELS:
            vdiv = self._read(f"{channel}-vdiv", _positive_float, f"{channel} V/div")
            offset = self._read(f"{channel}-offset", _finite_float, f"{channel} offset")
            coupling = Coupling(self._selected(f"{channel}-coupling", f"{channel} coupling"))
            enabled = self.query_one(f"#{channel}-enable", Checkbox).value
            settings.append(ChannelSettings(channel, enabled, vdiv, offset, coupling))
        return settings

    def _read_trigger_settings(self) -> TriggerSettings:
        level = self._read("trig-level", _finite_float, "Trigger level")
        time_per_div = self._read("trig-tdiv", _positive_float, "Time/div")
        source = Channel(self._selected("trig-source", "Trigger source"))
        mode = TriggerMode(self._selected("trig-mode", "Trigger mode"))
        slope = TriggerSlope(self._selected("trig-slope", "Trigger slope"))
        coupling = TriggerCoupling(self._selected("trig-coupling", "Trigger coupling"))
        return TriggerSettings(source, mode, slope, coupling, level, time_per_div)

    # -- retrieve current settings ------------------------------------------

    def _start_retrieve(self, *, silent: bool = False) -> None:
        handle = self._scope_handle()
        if handle is None or self._busy:
            return
        self._set_busy(True)
        if not silent:
            self.query_one("#scope-log", RichLog).write("Retrieving current settings…")
        self._retrieve(handle.device, silent)

    @work(thread=True, exclusive=True, group="scope-retrieve", exit_on_error=False)
    def _retrieve(self, scope: LeCroy, silent: bool) -> None:
        channels: list[ChannelSettings] = []
        trigger: TriggerSettings | None = None
        error: Exception | None = None
        try:
            channels = read_channel_settings(scope, CHANNELS)
            trigger = read_trigger_settings(scope)
        except Exception as exc:  # noqa: BLE001 - never leave the page busy
            log.exception("scope: failed to read settings")
            error = exc
        self._ui(self._finish_retrieve, scope, channels, trigger, error, silent)

    def _finish_retrieve(
        self,
        scope: LeCroy,
        channels: Sequence[ChannelSettings],
        trigger: TriggerSettings | None,
        error: Exception | None,
        silent: bool,
    ) -> None:
        if not self._scope_is_current(scope):
            self._set_busy(False)
            return
        log_widget = self.query_one("#scope-log", RichLog)
        if error is not None or trigger is None:
            self._set_busy(False)
            log_widget.write(f"[red]Retrieve failed: {escape(one_line(error))}[/red]")
            if not silent:
                self.notify("Couldn't read the scope settings — see the log.", severity="warning")
            return
        for settings in channels:
            self._show_channel(settings)
        self._show_trigger(trigger)
        self._channel_state = tuple(channels)
        self._trigger_state = trigger
        self._set_busy(False)
        log_widget.write("Scope settings synchronized from the instrument.")

    # -- apply: write what changed, then show what the scope reports ---------

    def _start_apply(self) -> None:
        handle = self._scope_handle()
        if handle is None:
            return
        if self._busy:
            self.notify("Another scope operation is already running.", severity="warning")
            return
        if self._channel_state is None or self._trigger_state is None:
            self.notify("Retrieve current settings before applying changes.", severity="warning")
            return
        try:
            channels = self._read_channel_settings()
            trigger = self._read_trigger_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid scope settings: {exc}", severity="error", markup=False)
            return
        known = {setting.channel: setting for setting in self._channel_state}
        changed = [setting for setting in channels if setting != known[setting.channel]]
        trigger_changed = trigger != self._trigger_state
        if not changed and not trigger_changed:
            self.query_one("#scope-log", RichLog).write("No changes to apply.")
            return
        self._set_busy(True)
        self._apply(
            handle.device,
            changed,
            trigger if trigger_changed else None,
            self._channel_state,
            self._trigger_state,
        )

    @work(thread=True, exclusive=True, group="scope-apply", exit_on_error=False)
    def _apply(
        self,
        scope: LeCroy,
        channels: Sequence[ChannelSettings],
        trigger: TriggerSettings | None,
        channel_baseline: Sequence[ChannelSettings],
        trigger_baseline: TriggerSettings,
    ) -> None:
        verified_channels: list[ChannelSettings] = []
        verified_trigger: TriggerSettings | None = None
        error: Exception | None = None
        try:
            if channels:
                verified_channels = apply_channel_settings(
                    scope, channels, current_settings=channel_baseline
                )
            if trigger is not None:
                apply_trigger_settings(scope, trigger, current_settings=trigger_baseline)
                verified_trigger = read_trigger_settings(scope)
        except Exception as exc:  # noqa: BLE001 - never leave the page busy
            log.exception("scope: applying settings failed")
            error = exc
        self._ui(
            self._finish_apply,
            scope,
            channels,
            trigger,
            verified_channels,
            verified_trigger,
            error,
        )

    def _finish_apply(
        self,
        scope: LeCroy,
        channels: Sequence[ChannelSettings],
        trigger: TriggerSettings,
        verified_channels: Sequence[ChannelSettings],
        verified_trigger: TriggerSettings | None,
        error: Exception | None,
    ) -> None:
        if not self._scope_is_current(scope):
            self._set_busy(False)
            return
        log_widget = self.query_one("#scope-log", RichLog)
        if error is not None:
            # What the scope holds is now unknown (some writes may have landed):
            # keep the form as typed and require a Retrieve before the next action.
            self._channel_state = None
            self._trigger_state = None
            self._set_busy(False)
            log_widget.write(f"[red]Apply failed: {escape(one_line(error))}[/red]")
            self.notify(
                f"Applying settings failed: {one_line(error)}; retrieve to resync.",
                severity="error",
                markup=False,
            )
            return

        # What the scope *reported* is the truth. Requested values are never assumed
        # to have been applied: it rounds V/div and offset, and can ignore a command.
        requested = {setting.channel: setting for setting in channels}
        known = {setting.channel: setting for setting in self._channel_state or ()}
        problems: list[str] = []
        for actual in verified_channels:
            known[actual.channel] = actual
            self._show_channel(actual)
            want = requested[actual.channel]
            if actual.enabled != want.enabled:
                problems.append(
                    f"{actual.channel} trace is {'ON' if actual.enabled else 'OFF'} "
                    f"but {'ON' if want.enabled else 'OFF'} was requested"
                )
            if actual.coupling != want.coupling:
                problems.append(
                    f"{actual.channel} coupling is {actual.coupling.value} "
                    f"but {want.coupling.value} was requested"
                )
            for name, label in (("volts_per_div", "V/div"), ("offset", "offset")):
                asked, got = getattr(want, name), getattr(actual, name)
                if not math.isclose(asked, got, rel_tol=1e-3, abs_tol=1e-6):
                    log_widget.write(
                        f"[yellow]{actual.channel} {label}: requested {asked:g}, "
                        f"scope set {got:g}[/yellow]"
                    )
        self._channel_state = tuple(known[channel] for channel in CHANNELS if channel in known)
        if verified_trigger is not None:
            self._show_trigger(verified_trigger)
            self._trigger_state = verified_trigger
            wanted, actual_tdiv = trigger.time_per_div, verified_trigger.time_per_div
            if (
                wanted is not None
                and actual_tdiv is not None
                and abs(wanted - actual_tdiv) > 1e-3 * wanted
            ):
                log_widget.write(
                    f"[yellow]Time/div: requested {format_si(wanted, 's')}, "
                    f"scope uses {format_si(actual_tdiv, 's')}.[/yellow]"
                )
        self._set_busy(False)
        if problems:
            for problem in problems:
                log_widget.write(f"[red]{escape(problem)}[/red]")
            self.notify("Some channel changes failed — see the log.", severity="error")
            return
        log_widget.write("Applied and verified changes.")

    # -- acquire: record enabled channels, persist the result, then plot ---

    def _start_acquire(self) -> None:
        handle = self._scope_handle()
        if handle is None:
            return
        scope = handle.device
        if self._busy or not self._synced:
            self.notify(
                "Scope settings are not synchronized yet; retrieve the current settings first.",
                severity="warning",
            )
            return
        try:
            channel_settings = tuple(self._read_channel_settings())
            trigger_settings = self._read_trigger_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid scope configuration: {exc}", severity="error", markup=False)
            return
        if channel_settings != self._channel_state or trigger_settings != self._trigger_state:
            self.notify(
                "The form has changes the scope doesn't have: press Apply "
                "(or Retrieve to discard them) before acquiring.",
                severity="warning",
            )
            return
        channels = [setting.channel for setting in channel_settings if setting.enabled]
        if not channels:
            self.notify("Enable at least one channel first.", severity="warning")
            return
        self.query_one("#acquire-waveforms", Button).disabled = True
        log_widget = self.query_one("#scope-log", RichLog)
        log_widget.write("Acquiring " + ", ".join(str(c) for c in channels) + "…")
        self._acquire(
            scope,
            channels,
            channel_settings,
            trigger_settings,
            self._channel_state,
            self._trigger_state,
        )

    @work(thread=True, exclusive=True, group="scope-acquire", exit_on_error=False)
    def _acquire(
        self,
        scope: LeCroy,
        channels: Sequence[Channel],
        channel_settings: Sequence[ChannelSettings],
        trigger_settings: TriggerSettings,
        applied_channel_settings: Sequence[ChannelSettings] | None,
        applied_trigger_settings: TriggerSettings | None,
    ) -> None:
        handle = self.iyzee_app.handles.get("scope")
        if handle is None or handle.device is not scope:
            self._ui(self._acquire_failed, "the scope was disconnected before acquisition started")
            return
        try:
            recording = acquire_scope_recording(
                scope,
                channels,
                channel_settings=channel_settings,
                trigger_settings=trigger_settings,
                applied_channel_settings=applied_channel_settings,
                applied_trigger_settings=applied_trigger_settings,
            )
        except Exception as exc:  # noqa: BLE001 - never leave the Acquire button disabled
            log.exception("scope: acquisition failed")
            self._ui(self._acquire_failed, one_line(exc))
            return
        # acquire_scope_recording releases the instrument lock before this
        # point, so persistence cannot block another caller from using the scope.
        path = None
        save_error: Exception | None = None
        try:
            path = save_scope_acquisition(recording, create_dirs())
        except Exception as exc:  # noqa: BLE001 - acquisition stays available in memory
            log.exception("scope: failed to save acquisition")
            save_error = exc
        # Reduce to terminal-sized data before crossing back to the UI thread.
        # The full-resolution recording remains untouched for later analysis;
        # only the disposable preview is reduced.
        series, xlabel = time_series(
            [(w.time, w.values, str(w.channel)) for w in recording.waveforms],
            recording.waveforms[0].time_unit if recording.waveforms else "S",
        )
        self._ui(self._finish_acquire, recording, series, xlabel, path, save_error)

    def _acquire_failed(self, reason: str) -> None:
        self.query_one("#acquire-waveforms", Button).disabled = False
        self.query_one("#scope-log", RichLog).write(
            f"[red]Acquisition failed: {escape(reason)}[/red]"
        )
        self.notify(f"Acquisition failed: {reason}", severity="error", markup=False)

    def _finish_acquire(
        self,
        recording: ScopeAcquisition,
        series: list[tuple[list[float], list[float], str | None]],
        xlabel: str,
        path: Path | None,
        save_error: Exception | None,
    ) -> None:
        self.query_one("#acquire-waveforms", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        if recording.frozen:
            log_widget.write(
                "Acquisition was running: paused for a consistent capture, then resumed."
            )
        for warning in recording.warnings:
            log_widget.write(f"[yellow]{escape(warning)}[/yellow]")
        if recording.waveforms:
            plot = self.query_one("#scope-plot", PlotextPlot)
            first = recording.waveforms[0]
            draw_series(
                plot,
                series,
                title="Scope waveforms",
                xlabel=xlabel,
                ylabel=f"Signal ({first.value_unit})",
            )
            log_widget.write(f"Acquired {len(recording.waveforms)} channel(s).")
            for line in describe_timebase(
                first.time_offset, first.time_interval, len(first.time), first.time_unit
            ):
                log_widget.write(line)
        if path is not None:
            log_widget.write(f"Saved acquisition: {escape(str(path))}")
        if save_error is not None:
            self.notify(
                f"Acquired traces but could not save them: {one_line(save_error)}",
                severity="error",
                markup=False,
            )
