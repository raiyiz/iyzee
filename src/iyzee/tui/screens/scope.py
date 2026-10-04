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
from collections.abc import Iterable, Sequence
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
    ChannelApplyResult,
    ChannelError,
    ChannelSettings,
    ScopeAcquisition,
    TriggerSettings,
    acquire_scope_recording,
    apply_and_verify_channel_settings,
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

# The form fields whose edits are tracked against what the scope last reported.
_CHANNEL_FIELD_NAMES = ("enable", "vdiv", "offset", "coupling")
TRIGGER_FIELDS = (
    "trig-source",
    "trig-mode",
    "trig-slope",
    "trig-coupling",
    "trig-level",
    "trig-tdiv",
)


def channel_fields(channel: Channel) -> tuple[str, ...]:
    """Widget ids of one channel's tracked form fields."""
    return tuple(f"{channel}-{name}" for name in _CHANNEL_FIELD_NAMES)


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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Set here, not in on_mount: refresh_readiness() and the Changed handlers
        # can run before the page is mounted.
        self._last_applied_channel_settings: tuple[ChannelSettings, ...] | None = None
        self._last_applied_trigger_settings: TriggerSettings | None = None
        self._settings_synced = False
        self._settings_busy = False
        self._retrieve_in_flight = False
        self._suppress_dirty_events = False
        self._dirty_fields: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Static("Scope", classes="panel-title")
        # Shown only while the scope isn't connected; see refresh_readiness().
        yield Static("", id="scope-status")
        yield Static("Not synchronized", id="scope-sync-status", classes="scope-sync-status")
        yield Button("Retrieve current settings", id="retrieve-settings", variant="primary")
        with Grid(id="scope-channels"):
            for channel in CHANNELS:
                yield _channel_panel(channel)
        yield Button("Apply channel changes", id="apply-channels")
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
            yield Button("Apply trigger changes", id="apply-trigger")
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

    # -- form state ---------------------------------------------------------

    def _field_label(self, field_id: str) -> str:
        """Human-readable label for the small field-level dirty indicator."""
        if field_id.startswith("C"):
            channel, field = field_id.split("-", 1)
            return {
                "enable": f"{channel} visibility",
                "vdiv": f"{channel} V/div",
                "offset": f"{channel} offset",
                "coupling": f"{channel} coupling",
            }[field]
        return {
            "trig-source": "trigger source",
            "trig-mode": "trigger mode",
            "trig-slope": "trigger slope",
            "trig-coupling": "trigger coupling",
            "trig-level": "trigger level",
            "trig-tdiv": "time/div",
        }.get(field_id, field_id)

    def _channel_baseline(self, channel: Channel) -> ChannelSettings | None:
        return next(
            (
                setting
                for setting in self._last_applied_channel_settings or ()
                if setting.channel == channel
            ),
            None,
        )

    def _float_differs(self, field_id: str, parse, label: str, baseline: float) -> bool:
        """True if the field differs from ``baseline`` (or doesn't parse at all)."""
        try:
            return self._read(field_id, parse, label) != baseline
        except FieldError:
            return True

    def _field_changed_from_baseline(self, field_id: str) -> bool:
        """Compare one form field with the known instrument-state baseline."""
        if field_id.startswith("C"):
            name, field = field_id.split("-", 1)
            base = self._channel_baseline(Channel(name))
            if base is None:
                return False
            if field == "enable":
                return self.query_one(f"#{field_id}", Checkbox).value != base.enabled
            if field == "vdiv":
                label = f"{name} V/div"
                return self._float_differs(field_id, _positive_float, label, base.volts_per_div)
            if field == "offset":
                return self._float_differs(field_id, _finite_float, f"{name} offset", base.offset)
            if field == "coupling":
                return self.query_one(f"#{field_id}", Select).value != base.coupling.value
            return False
        trig = self._last_applied_trigger_settings
        if trig is None:
            return False
        if field_id == "trig-level":
            return self._float_differs(field_id, _finite_float, "Trigger level", trig.level_volts)
        if field_id == "trig-tdiv":
            return trig.time_per_div is not None and self._float_differs(
                field_id, _positive_float, "Time/div", trig.time_per_div
            )
        selects = {
            "trig-source": trig.source,
            "trig-mode": trig.mode,
            "trig-slope": trig.slope,
            "trig-coupling": trig.coupling,
        }
        if field_id in selects:
            return self.query_one(f"#{field_id}", Select).value != selects[field_id].value
        return False

    def _refresh_field_dirty(self, field_id: str) -> None:
        dirty = self._field_changed_from_baseline(field_id)
        (self._dirty_fields.add if dirty else self._dirty_fields.discard)(field_id)
        self.query_one(f"#{field_id}").set_class(dirty, "scope-dirty")
        self._refresh_scope_ui()

    def _set_channel_dirty_style(self, channel: Channel) -> None:
        dirty = any(field_id.startswith(f"{channel}-") for field_id in self._dirty_fields)
        self.query_one(f"#{channel}-panel", Vertical).set_class(dirty, "scope-dirty")

    def _refresh_scope_ui(self) -> None:
        """Update dirty styling, status text, and which actions can run."""
        for channel in CHANNELS:
            self._set_channel_dirty_style(channel)
        trigger_dirty = any(field_id.startswith("trig-") for field_id in self._dirty_fields)
        self.query_one("#scope-trigger", Vertical).set_class(trigger_dirty, "scope-dirty")
        status = self.query_one("#scope-sync-status", Static)
        if self._settings_busy:
            status.update("Synchronizing scope settings…")
            status.add_class("scope-dirty")
        elif not self._settings_synced:
            missing_channels = [
                str(channel) for channel in CHANNELS if self._channel_baseline(channel) is None
            ]
            missing = missing_channels + (
                [] if self._last_applied_trigger_settings is not None else ["trigger"]
            )
            detail = f" — missing: {', '.join(missing)}" if missing else ""
            status.update("Scope state is not fully synchronized" + detail + ".")
            status.remove_class("scope-dirty")
        elif self._dirty_fields:
            labels = [self._field_label(field_id) for field_id in sorted(self._dirty_fields)]
            status.update("Local edits pending: " + ", ".join(labels) + ".")
            status.add_class("scope-dirty")
        else:
            status.update("Scope state synchronized — no pending changes.")
            status.remove_class("scope-dirty")

        busy = self._settings_busy
        channel_dirty = any(field_id.startswith("C") for field_id in self._dirty_fields)
        trigger_dirty = any(field_id.startswith("trig-") for field_id in self._dirty_fields)
        self.query_one("#apply-channels", Button).disabled = busy or not channel_dirty
        self.query_one("#apply-trigger", Button).disabled = busy or not trigger_dirty
        self.query_one("#acquire-waveforms", Button).disabled = (
            busy or not self._settings_synced or bool(self._dirty_fields)
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        super().on_input_changed(event)
        if self._suppress_dirty_events or event.input.id is None:
            return
        if event.input.id.startswith(("C", "trig-")):
            self._refresh_field_dirty(event.input.id)

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._suppress_dirty_events or event.select.id is None:
            return
        if event.select.id.startswith(("C", "trig-")):
            self._refresh_field_dirty(event.select.id)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if self._suppress_dirty_events or event.checkbox.id is None:
            return
        if event.checkbox.id.startswith("C"):
            self._refresh_field_dirty(event.checkbox.id)

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
            self._dirty_fields.clear()
            for widget in self.query(".scope-dirty"):
                widget.remove_class("scope-dirty")
            status.update("Not ready: connect the Scope first — press F1 for the Connect page.")
        elif not self._settings_synced and not self._retrieve_in_flight:
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

    def _begin_settings_op(self, button_id: str, *, retrieve: bool = False) -> None:
        """Mark a settings operation as running and lock out its own button."""
        self._settings_busy = True
        self._retrieve_in_flight = retrieve
        self.query_one(f"#{button_id}", Button).disabled = True

    def _end_settings_op(self, button_id: str) -> None:
        self._settings_busy = False
        self._retrieve_in_flight = False
        self.query_one(f"#{button_id}", Button).disabled = False

    def _mark_clean(self, field_ids: Iterable[str]) -> None:
        """These fields now match what the scope reported: they are no longer 'edited'."""
        for field_id in field_ids:
            self._dirty_fields.discard(field_id)
            self.query_one(f"#{field_id}").remove_class("scope-dirty")

    def commands(self) -> list[Command]:
        groups = {"channels": "apply-channels", "trigger": "apply-trigger"}

        def apply(args: list[str]) -> str:
            chosen = args or list(groups)
            unknown = [a for a in chosen if a not in groups]
            if unknown:
                raise CommandError(f"usage: :apply [channels|trigger]  (not {unknown[0]!r})")
            started = []
            for name in chosen:
                button = self.query_one(f"#{groups[name]}", Button)
                if not button.disabled:
                    button.press()
                    started.append(name)
            if not started:
                raise CommandError("nothing to apply (no changes, or the scope is not connected)")
            return f"applying {' and '.join(started)}…"

        def tdiv(args: list[str]) -> str:
            if len(args) != 1:
                raise CommandError("usage: :tdiv <seconds/div>, e.g. :tdiv 2u")
            seconds = parse_si(args[0])
            if seconds <= 0:
                raise CommandError("time/div must be positive")
            self.query_one("#trig-tdiv", Input).value = repr(seconds)
            self._refresh_field_dirty("trig-tdiv")  # so Apply is enabled before we press it
            return self.press_button("apply-trigger", f"set time/div to {format_si(seconds, 's')}")

        return [
            Command("retrieve", lambda _: self.press_button("retrieve-settings", "retrieve settings"),
                    "Read the scope's current settings into the form"),
            Command("apply", apply, "Send changed channel/trigger settings to the scope",
                    usage="[channels|trigger]", complete=lambda p: [g for g in groups if g.startswith(p)]),
            Command("acquire", lambda _: self.press_button("acquire-waveforms", "acquire waveforms"),
                    "Acquire waveforms from the enabled channels"),
            Command("tdiv", tdiv, "Set time/div and apply it (2u, 500n, 1.5m, 1e-6)", usage="<seconds/div>"),
        ]

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
        handle = self._scope_handle()
        if handle is None or self._settings_busy:
            return
        self._begin_settings_op("retrieve-settings", retrieve=True)
        if not silent:
            self.query_one("#scope-log", RichLog).write("Retrieving current settings…")
        self._retrieve(handle.device, handle.lock, silent)

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
            scope,
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
        if settings.time_per_div is not None:
            self.query_one("#trig-tdiv", Input).value = str(settings.time_per_div)
            self.query_one("#trig-tdiv", Input).remove_class("-invalid")

    def _finish_retrieve(
        self,
        scope: LeCroy,
        channel_settings: Sequence[ChannelSettings],
        channel_errors: Sequence[ChannelError],
        trigger_settings: TriggerSettings | None,
        trigger_error: Exception | None,
        silent: bool,
    ) -> None:
        self._end_settings_op("retrieve-settings")
        if not self._scope_is_current(scope):
            self._refresh_scope_ui()
            return
        log_widget = self.query_one("#scope-log", RichLog)
        self._suppress_dirty_events = True
        try:
            for settings in channel_settings:
                self._apply_retrieved_channel_settings(settings)
            if trigger_settings is not None:
                self._apply_retrieved_trigger_settings(trigger_settings)
        finally:
            self._suppress_dirty_events = False
        for settings in channel_settings:
            self._mark_clean(channel_fields(settings.channel))
        if trigger_settings is not None:
            self._mark_clean(TRIGGER_FIELDS)
        for err in channel_errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
        self._last_applied_channel_settings = tuple(channel_settings) or None
        self._last_applied_trigger_settings = trigger_settings
        self._settings_synced = not channel_errors and trigger_error is None
        if self._settings_synced:
            log_widget.write("Scope settings synchronized from the instrument.")
        else:
            self._refresh_scope_ui()
            if not silent:
                self.notify(
                    "Some settings couldn't be retrieved — see the log; values that did read "
                    "back are filled in and the others are left alone.",
                    severity="warning",
                )
            return
        self._refresh_scope_ui()

    # -- channel settings ------------------------------------------------
    def _read_channel_settings(self) -> list[ChannelSettings]:
        settings = []
        for channel in CHANNELS:
            vdiv = self._read(f"{channel}-vdiv", _positive_float, f"{channel} V/div")
            offset = self._read(f"{channel}-offset", _finite_float, f"{channel} offset")
            coupling = Coupling(self._selected(f"{channel}-coupling", f"{channel} coupling"))
            enabled = self.query_one(f"#{channel}-enable", Checkbox).value
            settings.append(ChannelSettings(channel, enabled, vdiv, offset, coupling))
        return settings

    def _start_apply_channels(self) -> None:
        handle = self._scope_handle()
        if handle is None:
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
            self._refresh_scope_ui()
            return
        self._begin_settings_op("apply-channels")
        self._apply_channels(handle.device, changed, baseline, handle.lock)

    @work(thread=True, exclusive=True, group="scope-apply-channels", exit_on_error=False)
    def _apply_channels(
        self,
        scope: LeCroy,
        settings: Sequence[ChannelSettings],
        baseline: Sequence[ChannelSettings],
        lock,
    ) -> None:
        try:
            result = apply_and_verify_channel_settings(
                scope, settings, current_settings=baseline, lock=lock
            )
        except Exception as exc:  # noqa: BLE001 - never leave the Apply button disabled
            log.exception("scope: applying channel settings failed")
            result = ChannelApplyResult(
                (), tuple(ChannelError(s.channel, exc) for s in settings), ()
            )
        self._ui(self._finish_apply_channels, scope, settings, result, baseline)

    def _finish_apply_channels(
        self,
        scope: LeCroy,
        settings: Sequence[ChannelSettings],
        result: ChannelApplyResult,
        baseline: Sequence[ChannelSettings],
    ) -> None:
        self._end_settings_op("apply-channels")
        if not self._scope_is_current(scope):
            self._refresh_scope_ui()
            return
        log_widget = self.query_one("#scope-log", RichLog)
        error_channels = {err.channel for err in result.errors}
        # The baseline is what the scope *reported* after the writes. Requested
        # values are never assumed to have been applied: the scope rounds
        # V/div and offset, and can ignore a command outright.
        known = {setting.channel: setting for setting in baseline}
        for verified in result.verified:
            known[verified.channel] = verified
        self._last_applied_channel_settings = (
            tuple(known[channel] for channel in CHANNELS if channel in known) or None
        )
        for verified in result.verified:
            channel = verified.channel
            if channel in error_channels:
                # Keep what the user typed so they can retry; just re-evaluate
                # "changed" against the now-truthful baseline.
                for field_id in channel_fields(channel):
                    self._refresh_field_dirty(field_id)
                continue
            self._apply_retrieved_channel_settings(verified)  # shows any rounding
            self._mark_clean(channel_fields(channel))
        self._settings_synced = (
            all(self._channel_baseline(channel) is not None for channel in CHANNELS)
            and self._last_applied_trigger_settings is not None
        )
        for adjustment in result.adjustments:
            label = "V/div" if adjustment.field == "volts_per_div" else "offset"
            log_widget.write(
                f"[yellow]{adjustment.channel} {label}: requested {adjustment.requested:g}, "
                f"scope set {adjustment.actual:g}[/yellow]"
            )
        if result.errors:
            for err in result.errors:
                log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
            self.notify("Some channel changes failed — see the log.", severity="error")
            self._refresh_scope_ui()
            return
        self._refresh_scope_ui()
        log_widget.write(
            "Applied and verified channel fields: "
            + ", ".join(str(setting.channel) for setting in settings)
            + "."
        )

    # -- trigger settings --------------------------------------------------

    def _read_trigger_settings(self) -> TriggerSettings:
        level = self._read("trig-level", _finite_float, "Trigger level")
        time_per_div = self._read("trig-tdiv", _positive_float, "Time/div")
        source = Channel(self._selected("trig-source", "Trigger source"))
        mode = TriggerMode(self._selected("trig-mode", "Trigger mode"))
        slope = TriggerSlope(self._selected("trig-slope", "Trigger slope"))
        coupling = TriggerCoupling(self._selected("trig-coupling", "Trigger coupling"))
        return TriggerSettings(source, mode, slope, coupling, level, time_per_div)

    def _start_apply_trigger(self) -> None:
        handle = self._scope_handle()
        if handle is None:
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
            self._refresh_scope_ui()
            return
        self._begin_settings_op("apply-trigger")
        self._apply_trigger(handle.device, settings, baseline, handle.lock)

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
        self._ui(
            self._finish_apply_trigger,
            scope,
            settings,
            error,
            verified,
            verification_error,
        )

    def _finish_apply_trigger(
        self,
        scope: LeCroy,
        settings: TriggerSettings,
        error: Exception | None,
        verified: TriggerSettings | None,
        verification_error: Exception | None,
    ) -> None:
        self._end_settings_op("apply-trigger")
        if not self._scope_is_current(scope):
            self._refresh_scope_ui()
            return
        log_widget = self.query_one("#scope-log", RichLog)
        if error is not None:
            self._settings_synced = False
            log_widget.write(f"[red]Trigger: {escape(one_line(error))}[/red]")
            self.notify(
                f"Trigger settings failed: {one_line(error)}", severity="error", markup=False
            )
            self._refresh_scope_ui()
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
            self._refresh_scope_ui()
            return
        self._suppress_dirty_events = True
        try:
            self._apply_retrieved_trigger_settings(verified)
        finally:
            self._suppress_dirty_events = False
        self._last_applied_trigger_settings = verified
        self._settings_synced = all(
            self._channel_baseline(channel) is not None for channel in CHANNELS
        )
        self._mark_clean(TRIGGER_FIELDS)
        self._refresh_scope_ui()
        log_widget.write("Trigger settings applied and verified.")
        wanted, actual = settings.time_per_div, verified.time_per_div
        if wanted is not None and actual is not None and abs(wanted - actual) > 1e-3 * wanted:
            log_widget.write(
                f"Time/div: requested {format_si(wanted, 's')}, scope uses {format_si(actual, 's')}."
            )

    # -- acquire: record enabled channels, persist the result, then plot ---

    def _start_acquire(self) -> None:
        handle = self._scope_handle()
        if handle is None:
            return
        scope = handle.device
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
                lock=handle.lock,
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
        for err in recording.errors:
            log_widget.write(f"[red]{err.channel}: {escape(one_line(err.error))}[/red]")
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
        elif recording.errors and not recording.waveforms:
            self.notify("Acquisition failed — see the log.", severity="error")
