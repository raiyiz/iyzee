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

The fields start at sensible defaults on open (matching ``SweepScreen``'s
form, which does the same for its own device-side parameters) — this
screen has no way to know the scope is even connected yet at that point.
"Retrieve current settings" is what syncs the form to the scope's actual
state on demand: press it before making a small change, so "Apply" only
overwrites the one field you meant to touch instead of pushing out
whatever the other fields happened to default to.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static
from textual_plotext import PlotextPlot

from ...experiment import create_dirs
from ...scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope
from ...scope_workflows import (
    ChannelError,
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
from ..plotting import draw_series
from ..text import one_line
from .page import FieldError, Page, _field, _finite_float, _positive_float

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
    one trigger section, independent Apply buttons, and an "Acquire & save"
    action that records the selected waveforms before plotting them.
    """

    def compose(self) -> ComposeResult:
        yield Static("Scope", classes="panel-title")
        # Shown only while the scope isn't connected; see refresh_readiness().
        yield Static("", id="scope-status")
        yield Static("Not synchronized", id="scope-sync-status", classes="scope-sync-status")
        yield Button("Retrieve current settings", id="retrieve-settings", variant="primary")
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
            yield Button("Acquire & save", id="acquire-waveforms", variant="success")
        yield PlotextPlot(id="scope-plot")
        yield RichLog(id="scope-log", highlight=False, markup=True)

    def on_mount(self) -> None:
        plot = self.query_one("#scope-plot", PlotextPlot)
        plot.plt.title("Scope waveforms")
        plot.plt.xlabel("Time (s)")
        plot.plt.ylabel("Voltage (V)")
        self._last_applied_channel_settings: tuple[ChannelSettings, ...] | None = None
        self._last_applied_trigger_settings: TriggerSettings | None = None
        self._settings_synced = False
        self._settings_busy = False
        self._retrieve_in_flight = False
        self.refresh_readiness()
        self._update_sync_status()
        self._update_apply_buttons()

    def on_show(self) -> None:
        self.refresh_readiness()

    # -- form state ---------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        super().on_input_changed(event)
        if event.input.id and event.input.id.startswith(("C", "trig-")):
            self._mark_scope_dirty()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id and event.select.id.startswith(("C", "trig-")):
            self._mark_scope_dirty()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id and event.checkbox.id.startswith("C"):
            self._mark_scope_dirty()


    def _mark_scope_dirty(self) -> None:
        """Refresh the visible sync/dirty state from the known instrument baseline."""
        if not getattr(self, "_settings_synced", False):
            self._update_sync_status()
            return
        try:
            current_channels = self._read_channel_settings()
        except FieldError:
            current_channels = []
        baseline_channels = self._last_applied_channel_settings or ()
        known = {setting.channel: setting for setting in baseline_channels}
        for setting in current_channels:
            panel = self.query_one(f"#{setting.channel}-panel", Vertical)
            panel.set_class(setting != known.get(setting.channel), "scope-dirty")
            for field_id in (
                f"{setting.channel}-enable",
                f"{setting.channel}-vdiv",
                f"{setting.channel}-offset",
                f"{setting.channel}-coupling",
            ):
                widget = self.query_one(f"#{field_id}")
                widget.set_class(setting != known.get(setting.channel), "scope-dirty")
        try:
            trigger = self._read_trigger_settings()
        except FieldError:
            trigger = None
        trigger_dirty = trigger is not None and trigger != self._last_applied_trigger_settings
        self.query_one("#scope-trigger", Vertical).set_class(trigger_dirty, "scope-dirty")
        self._update_sync_status()
        self._update_apply_buttons()

    def _update_apply_buttons(self) -> None:
        busy = self._settings_busy or not self._settings_synced
        channel_dirty = False
        if not busy and self._last_applied_channel_settings is not None:
            try:
                current = self._read_channel_settings()
            except FieldError:
                current = []
            baseline = {setting.channel: setting for setting in self._last_applied_channel_settings}
            channel_dirty = any(setting != baseline.get(setting.channel) for setting in current if setting.channel in baseline)
        trigger_dirty = False
        if not busy and self._last_applied_trigger_settings is not None:
            try:
                trigger_dirty = self._read_trigger_settings() != self._last_applied_trigger_settings
            except FieldError:
                pass
        self.query_one("#apply-channels", Button).disabled = not channel_dirty or busy
        self.query_one("#apply-trigger", Button).disabled = not trigger_dirty or busy

    def _update_sync_status(self) -> None:
        status = self.query_one("#scope-sync-status", Static)
        if self._settings_busy:
            status.update("Synchronizing scope settings…")
        elif not self._settings_synced:
            status.update("Scope state not synchronized — retrieve current settings before acquiring.")
        else:
            dirty_channels = []
            baseline = {setting.channel: setting for setting in self._last_applied_channel_settings or ()}
            try:
                current = self._read_channel_settings()
            except FieldError:
                current = []
            for setting in current:
                if setting != baseline.get(setting.channel):
                    dirty_channels.append(str(setting.channel))
            try:
                trigger_dirty = (
                    self._read_trigger_settings() != self._last_applied_trigger_settings
                )
            except FieldError:
                trigger_dirty = False
            if dirty_channels or trigger_dirty:
                parts = dirty_channels + ([ "trigger"] if trigger_dirty else [])
                status.update("Edited locally — " + ", ".join(parts) + " will be applied.")
            else:
                status.update("Scope state synchronized.")

    # -- readiness -----------------------------------------------------------

    def refresh_readiness(self) -> None:
        """Keep connection readiness and scope-state synchronization current.

        A newly connected scope is read automatically once so the form starts
        from the instrument's real state instead of its UI defaults. The
        explicit *Retrieve current settings* button remains useful after a
        front-panel change or whenever the user wants a fresh baseline for
        fine-grained edits.
        """
        status = self.query_one("#scope-status", Static)
        connected = "scope" in self.iyzee_app.handles
        if not connected:
            self._last_applied_channel_settings = None
            self._last_applied_trigger_settings = None
            self._settings_synced = False
            self._settings_busy = False
            self._retrieve_in_flight = False
            status.update("Not ready: connect the Scope first — press F1 for the Connect page.")
        elif not self._settings_synced and not self._retrieve_in_flight:
            self._start_retrieve(silent=True)
        status.display = not connected

    def _scope(self) -> LeCroy | None:
        """The live driver, or ``None`` (after notifying) if not connected."""
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self.notify(
                "Connect the scope first — press F1 for the Connect page.", severity="error"
            )
            return None
        return handle.device

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "retrieve-settings":
            self._start_retrieve()
        elif event.button.id == "apply-channels":
            self._start_apply_channels()
        elif event.button.id == "apply-trigger":
            self._start_apply_trigger()
        elif event.button.id == "acquire-waveforms":
            self._start_acquire()

    # -- retrieve current settings ------------------------------------------

    def _start_retrieve(self, *, silent: bool = False) -> None:
        scope = self._scope()
        if scope is None or self._settings_busy:
            return
        self._settings_busy = True
        self._retrieve_in_flight = True
        self.query_one("#retrieve-settings", Button).disabled = True
        if not silent:
            self.query_one("#scope-log", RichLog).write("Retrieving current settings…")
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self._settings_busy = False
            self._retrieve_in_flight = False
            self.query_one("#retrieve-settings", Button).disabled = False
            return
        self._retrieve(scope, handle.lock, silent)

    @work(thread=True, exclusive=True, group="scope-retrieve", exit_on_error=False)
    def _retrieve(self, scope: LeCroy, lock, silent: bool) -> None:
        channel_settings, channel_errors = read_channel_settings(scope, CHANNELS, lock=lock)
        trigger_settings: TriggerSettings | None = None
        trigger_error: Exception | None = None
        try:
            trigger_settings = read_trigger_settings(scope, lock=lock)
        except Exception as exc:  # noqa: BLE001
            log.exception("scope: failed to read trigger settings")
            trigger_error = exc
        self._ui(
            self._finish_retrieve,
            channel_settings,
            channel_errors,
            trigger_settings,
            trigger_error,
            silent,
        )

    def _apply_retrieved_channel_settings(self, settings: ChannelSettings) -> None:
        """Write one channel's retrieved values into its form fields.

        Only touches the fields for ``settings.channel`` — a channel that
        failed to read keeps whatever was already in its form.
        """
        channel = settings.channel
        self.query_one(f"#{channel}-enable", Checkbox).value = settings.enabled
        self.query_one(f"#{channel}-vdiv", Input).value = str(settings.volts_per_div)
        self.query_one(f"#{channel}-offset", Input).value = str(settings.offset)
        self.query_one(f"#{channel}-coupling", Select).value = settings.coupling.value
        for field_id in (f"{channel}-vdiv", f"{channel}-offset"):
            self.query_one(f"#{field_id}", Input).remove_class("-invalid")

    def _apply_retrieved_trigger_settings(self, settings: TriggerSettings) -> None:
        self.query_one("#trig-source", Select).value = settings.source.value
        self.query_one("#trig-mode", Select).value = settings.mode.value
        self.query_one("#trig-slope", Select).value = settings.slope.value
        self.query_one("#trig-coupling", Select).value = settings.coupling.value
        self.query_one("#trig-level", Input).value = str(settings.level_volts)
        self.query_one("#trig-level", Input).remove_class("-invalid")

    def _finish_retrieve(
        self,
        channel_settings: Sequence[ChannelSettings],
        channel_errors: Sequence[ChannelError],
        trigger_settings: TriggerSettings | None,
        trigger_error: Exception | None,
        silent: bool,
    ) -> None:
        self._settings_busy = False
        self._retrieve_in_flight = False
        self.query_one("#retrieve-settings", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        for settings in channel_settings:
            self._apply_retrieved_channel_settings(settings)
        for err in channel_errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
        self._last_applied_channel_settings = tuple(channel_settings) or None
        if trigger_settings is not None:
            self._apply_retrieved_trigger_settings(trigger_settings)
        self._last_applied_trigger_settings = trigger_settings
        self._settings_synced = not channel_errors and trigger_error is None
        if self._settings_synced:
            log_widget.write("Scope settings synchronized from the instrument.")
        else:
            self._mark_scope_dirty()
            if not silent:
                self.notify(
                    "Some settings couldn't be retrieved — see the log; values that did read "
                    "back are filled in and the others are left alone.",
                    severity="warning",
                )
            return
        self._mark_scope_dirty()

    # -- channel settings ------------------------------------------------
    def _read_channel_settings(self) -> list[ChannelSettings]:
        settings = []
        for channel in CHANNELS:
            vdiv = self._read(f"{channel}-vdiv", _positive_float, f"{channel} V/div")
            offset = self._read(f"{channel}-offset", _finite_float, f"{channel} offset")
            coupling = Coupling(self.query_one(f"#{channel}-coupling", Select).value)
            enabled = self.query_one(f"#{channel}-enable", Checkbox).value
            settings.append(ChannelSettings(channel, enabled, vdiv, offset, coupling))
        return settings

    def _start_apply_channels(self) -> None:
        scope = self._scope()
        if scope is None:
            return
        if self._settings_busy:
            self.notify("Another scope settings operation is already running.", severity="warning")
            return
        baseline = self._last_applied_channel_settings
        if baseline is None:
            self.notify(
                "Retrieve current settings before changing channel settings.", severity="warning"
            )
            return
        try:
            settings = self._read_channel_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid channel settings: {exc}", severity="error", markup=False)
            return
        known = {setting.channel: setting for setting in baseline}
        apply_settings = [setting for setting in settings if setting.channel in known]
        skipped = [setting.channel for setting in settings if setting.channel not in known]
        if skipped:
            self.notify(
                "Skipped unsynchronized channels: "
                + ", ".join(map(str, skipped))
                + ". Retrieve current settings for them first.",
                severity="warning",
            )
        changed = [setting for setting in apply_settings if setting != known[setting.channel]]
        if not changed:
            self.query_one("#scope-log", RichLog).write("No channel changes to apply.")
            self._mark_scope_dirty()
            return
        self._settings_busy = True
        self.query_one("#apply-channels", Button).disabled = True
        self._apply_channels(scope, changed, baseline)

    @work(thread=True, exclusive=True, group="scope-apply-channels", exit_on_error=False)
    def _apply_channels(
        self,
        scope: LeCroy,
        settings: Sequence[ChannelSettings],
        baseline: Sequence[ChannelSettings],
    ) -> None:
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self._ui(
                self._finish_apply_channels,
                settings,
                [ChannelError(settings[0].channel, RuntimeError("scope disconnected"))],
                baseline,
            )
            return
        errors = apply_channel_settings(
            scope, settings, current_settings=baseline, lock=handle.lock
        )
        self._ui(self._finish_apply_channels, settings, errors, baseline)

    def _finish_apply_channels(
        self,
        settings: Sequence[ChannelSettings],
        errors: Sequence[ChannelError],
        baseline: Sequence[ChannelSettings],
    ) -> None:
        self._settings_busy = False
        self.query_one("#apply-channels", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        error_channels = {err.channel for err in errors}
        known = {setting.channel: setting for setting in baseline}
        for setting in settings:
            if setting.channel not in error_channels:
                known[setting.channel] = setting
        self._last_applied_channel_settings = (
            tuple(known[channel] for channel in CHANNELS if channel in known) or None
        )
        if errors:
            self._settings_synced = False
            for err in errors:
                log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
            self.notify("Some channel changes failed — see the log.", severity="error")
            self._update_sync_status()
            self._update_apply_buttons()
            return
        self._settings_synced = True
        self._mark_scope_dirty()
        log_widget.write(
            "Applied changed channel fields: "
            + ", ".join(str(setting.channel) for setting in settings)
            + "."
        )

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
        if self._settings_busy:
            self.notify("Another scope settings operation is already running.", severity="warning")
            return
        baseline = self._last_applied_trigger_settings
        if baseline is None:
            self.notify(
                "Retrieve current settings before changing trigger settings.", severity="warning"
            )
            return
        try:
            settings = self._read_trigger_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid trigger settings: {exc}", severity="error", markup=False)
            return
        if settings == baseline:
            self.query_one("#scope-log", RichLog).write("No trigger changes to apply.")
            self._mark_scope_dirty()
            return
        self._settings_busy = True
        self.query_one("#apply-trigger", Button).disabled = True
        handle = self.iyzee_app.handles.get("scope")
        if handle is None:
            self._settings_busy = False
            self.query_one("#apply-trigger", Button).disabled = False
            return
        self._apply_trigger(scope, settings, baseline, handle.lock)

    @work(thread=True, exclusive=True, group="scope-apply-trigger", exit_on_error=False)
    def _apply_trigger(
        self,
        scope: LeCroy,
        settings: TriggerSettings,
        baseline: TriggerSettings,
        lock,
    ) -> None:
        error: Exception | None = None
        verified: TriggerSettings | None = None
        verification_error: Exception | None = None
        try:
            apply_trigger_settings(scope, settings, current_settings=baseline, lock=lock)
            verified = read_trigger_settings(scope, lock=lock)
        except Exception as exc:  # noqa: BLE001
            log.exception("scope: failed to apply trigger settings")
            error = exc
        if error is None and verified is None:
            verification_error = RuntimeError("trigger readback returned no settings")
        self._ui(self._finish_apply_trigger, settings, error, verified, verification_error)

    def _finish_apply_trigger(
        self,
        settings: TriggerSettings,
        error: Exception | None,
        verified: TriggerSettings | None,
        verification_error: Exception | None,
    ) -> None:
        self._settings_busy = False
        self.query_one("#apply-trigger", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        if error is not None:
            self._settings_synced = False
            log_widget.write(f"[red]Trigger: {escape(one_line(error))}[/red]")
            self.notify(
                f"Trigger settings failed: {one_line(error)}", severity="error", markup=False
            )
            self._update_sync_status()
            self._update_apply_buttons()
            return
        if verification_error is not None or verified is None:
            self._settings_synced = False
            log_widget.write(
                "[yellow]Trigger settings were written, but readback failed; "
                "retrieve current settings to resync.[/yellow]"
            )
            self.notify(
                "Trigger settings were applied but could not be verified.",
                severity="warning",
            )
            self._update_sync_status()
            self._update_apply_buttons()
            return
        self._last_applied_trigger_settings = verified
        self._apply_retrieved_trigger_settings(verified)
        self._settings_synced = True
        self._mark_scope_dirty()
        log_widget.write("Trigger settings applied and verified.")

    # -- acquire: record enabled channels, persist the result, then plot ---

    def _start_acquire(self) -> None:
        scope = self._scope()
        if scope is None:
            return
        if self._settings_busy or self._retrieve_in_flight or not self._settings_synced:
            self.notify(
                "Scope settings are not synchronized yet; retrieve the current settings first.",
                severity="warning",
            )
            return
        channels = [
            channel for channel in CHANNELS if self.query_one(f"#{channel}-enable", Checkbox).value
        ]
        if not channels:
            self.notify("Enable at least one channel first.", severity="warning")
            return
        try:
            channel_settings = tuple(self._read_channel_settings())
            trigger_settings = self._read_trigger_settings()
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid scope configuration: {exc}", severity="error", markup=False)
            return
        self.query_one("#acquire-waveforms", Button).disabled = True
        log_widget = self.query_one("#scope-log", RichLog)
        log_widget.write("Acquiring " + ", ".join(str(c) for c in channels) + "…")
        # Snapshot the requested and known-applied configuration before the
        # worker starts. The form may change while acquisition runs; the
        # manifest must describe the configuration associated with this capture.
        applied_channel_settings = self._last_applied_channel_settings
        applied_trigger_settings = self._last_applied_trigger_settings
        self._acquire(
            scope,
            channels,
            channel_settings,
            trigger_settings,
            applied_channel_settings,
            applied_trigger_settings,
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
        recording = acquire_scope_recording(
            scope,
            channels,
            channel_settings=channel_settings,
            trigger_settings=trigger_settings,
            applied_channel_settings=applied_channel_settings,
            applied_trigger_settings=applied_trigger_settings,
            lock=self.iyzee_app.handles["scope"].lock,
        )
        # acquire_scope_recording releases the instrument lock before this
        # point, so persistence cannot block another caller from using the scope.
        path = None
        save_error: Exception | None = None
        try:
            path = save_scope_acquisition(recording, create_dirs())
        except Exception as exc:  # noqa: BLE001 - acquisition stays available in memory
            log.exception("scope: failed to save acquisition")
            save_error = exc
        self._ui(self._finish_acquire, recording, path, save_error)

    def _finish_acquire(
        self,
        recording: ScopeAcquisition,
        path: Path | None,
        save_error: Exception | None,
    ) -> None:
        self.query_one("#acquire-waveforms", Button).disabled = False
        log_widget = self.query_one("#scope-log", RichLog)
        for err in recording.errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
        if recording.waveforms:
            plot = self.query_one("#scope-plot", PlotextPlot)
            series = [
                (waveform.time.tolist(), waveform.values.tolist(), str(waveform.channel))
                for waveform in recording.waveforms
            ]
            first = recording.waveforms[0]
            draw_series(
                plot,
                series,
                title="Scope waveforms",
                xlabel=f"Time ({first.time_unit})",
                ylabel=f"Signal ({first.value_unit})",
            )
            log_widget.write(f"Acquired {len(recording.waveforms)} channel(s).")
        if path is not None:
            log_widget.write(f"Saved acquisition: {escape(str(path))}")
        if save_error is not None:
            self.notify(
                f"Acquired traces but could not save them: {one_line(save_error)}",
                severity="error",
                markup=False,
            )
        elif recording.errors and not recording.waveforms:
            self.notify("Acquisition failed — see the log.", severity="error")
