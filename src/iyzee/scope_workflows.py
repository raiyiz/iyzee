"""Scope workflow operations: configuration, acquisition, and persistence
on top of the raw :class:`~iyzee.devices.scope.LeCroy` driver.

Plain functions and dataclasses, no Textual import — the same operations
``ScopeScreen``'s buttons trigger are usable identically from a script or
the IPython console::

    from iyzee.devices.scope import Channel, Coupling, LeCroy
    from iyzee.scope_workflows import ChannelSettings, apply_channel_settings

    scope = LeCroy()
    scope.connect("10.0.0.5")
    apply_channel_settings(scope, [ChannelSettings(Channel.C1, True, 0.5, 0.0, Coupling.DC_1M)])

``read_channel_settings()``/``read_trigger_settings()`` are the read
counterparts: they turn the scope's *current* state back into the same
``ChannelSettings``/``TriggerSettings`` dataclasses, so a caller can read
what's actually configured, change only the field it cares about, and
apply the result straight back — without re-typing every other field just
to avoid clobbering it. The parsing involved (turning replies like
``"5.00E-01V"`` into floats/enums) follows the LeCroy remote command
reference the rest of this module's setters are written against, but —
like those setters — has not been exercised against real hardware; verify
against your instrument before relying on it for anything safety-critical.

``lock`` on every function here is optional: a script with its own
private ``LeCroy`` (nothing else could be contending for it) doesn't need
one. Pass a handle's own lock (``instruments.InstrumentHandle.lock``) when
the same scope might be touched concurrently by something else — the
TUI's ``ScopeScreen`` does exactly this, and it's what makes calling these
same functions safe from the IPython console at the same time a screen is
mid-acquisition, or from two screens/scripts sharing one connected handle.
The lock is re-entrant and, for a ``ScopeHandle``, is the driver's own
transaction lock; every batch here also enters the driver's ``transaction_lock`` itself.

Two behaviours worth knowing before relying on a recording:

* ``apply_and_verify_channel_settings()`` reads every channel back after
  writing. The scope's reported values, not the request, are what should be
  treated as applied (it rounds V/div and offset, and can ignore a command).
* ``acquire_scope_recording(freeze=True)`` (the default) stops a *running*
  acquisition (trigger mode AUTO/NORMAL) for the download and restores the
  previous mode afterwards, so every channel comes from the same capture.
  Pass ``freeze=False`` to leave the trigger mode alone.

If the driver reports its connection lost (any timeout or framing error
invalidates it), batch loops stop and mark the remaining channels as not
attempted instead of continuing on a stream that can no longer be trusted.
"""

from __future__ import annotations

import contextlib
import logging
import math
import platform
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from .devices.scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope
from .experiment.core import utc_now
from .experiment.io import save_numeric_recording

log = logging.getLogger("iyzee.scope_workflows")


@dataclass(frozen=True)
class ChannelSettings:
    """One analog channel's vertical settings, ready to push to the scope."""

    channel: Channel
    enabled: bool
    volts_per_div: float
    offset: float
    coupling: Coupling


@dataclass(frozen=True)
class TriggerSettings:
    """The scope's trigger configuration, ready to push."""

    source: Channel
    mode: TriggerMode
    slope: TriggerSlope
    coupling: TriggerCoupling
    level_volts: float
    time_per_div: float | None = None
    """Horizontal scale in seconds/division. Lives here because the timebase
    is acquisition-wide, like the trigger; ``None`` leaves it untouched."""


@dataclass(frozen=True)
class ChannelError:
    """One channel's operation failed; carries which channel and why.

    Used by configuration and acquisition helpers so partial hardware failures
    can be reported without discarding successful channels. The acquisition
    path preserves these errors in the scientific recording manifest."""

    channel: Channel
    error: Exception


@dataclass(frozen=True)
class ScopeWaveform:
    """One channel's time series plus the scope data needed to interpret it.

    ``values`` are calibrated engineering-unit samples. ``raw_codes`` preserves
    the signed 16-bit waveform codes when the detailed LeCroy transfer path is
    available; the gain/offset fields describe the conversion used for
    ``values``."""

    channel: Channel
    time: np.ndarray
    values: np.ndarray
    raw_codes: np.ndarray | None
    value_unit: str
    time_unit: str
    time_offset: float
    time_interval: float
    vertical_gain: float | None
    vertical_offset: float | None
    stats: dict[str, float | int | None]


@dataclass(frozen=True)
class ScopeAcquisition:
    """In-memory scope record ready to be persisted as a scientific measurement.

    The object deliberately separates requested configuration from the subset
    known to have been applied successfully. A failed channel remains visible in
    ``errors`` while successful waveforms stay available for saving and plotting."""

    measurement_id: str
    started_at_utc: str
    completed_at_utc: str
    instrument_address: str | None
    socket_timeout_s: float | None
    requested_channel_settings: tuple[ChannelSettings, ...]
    requested_trigger_settings: TriggerSettings | None
    applied_channel_settings: tuple[ChannelSettings, ...] | None
    applied_trigger_settings: TriggerSettings | None
    waveforms: tuple[ScopeWaveform, ...]
    errors: tuple[ChannelError, ...]
    # Provenance. All optional so existing constructors keep working.
    instrument_id: str | None = None
    """``*IDN?`` reply (make, model, serial, firmware), if the scope answered."""
    frozen: bool = False
    """True if a running acquisition was stopped for the download, so every
    channel below comes from the same capture."""
    prior_trigger_mode: TriggerMode | None = None
    warnings: tuple[str, ...] = ()
    """Non-fatal problems (could not freeze / restore, identity unavailable)."""


@dataclass(frozen=True)
class SettingAdjustment:
    """A numeric setting the scope accepted but rounded to something else."""

    channel: Channel
    field: str
    requested: float
    actual: float


@dataclass(frozen=True)
class ChannelApplyResult:
    """Outcome of :func:`apply_and_verify_channel_settings`.

    ``verified`` is what the scope itself reported *after* the writes, not what
    was requested, so it is the right value to store as the new baseline and to
    record as the applied configuration.
    """

    verified: tuple[ChannelSettings, ...]
    errors: tuple[ChannelError, ...]
    adjustments: tuple[SettingAdjustment, ...]


def _stats(time: np.ndarray, values: np.ndarray) -> dict[str, float | int | None]:
    """Calculate descriptive statistics without modifying recorded samples.

    Non-finite values remain in the saved array but are excluded from the
    scalar summary statistics.
    """
    finite = np.isfinite(values)
    indices = np.flatnonzero(finite)
    base: dict[str, float | int | None] = {
        "sample_count": int(values.size),
        "finite_count": int(indices.size),
    }
    if indices.size == 0:
        return base | {
            "min": None,
            "max": None,
            "mean": None,
            "rms": None,
            "stddev": None,
            "peak_to_peak": None,
            "max_abs": None,
            "min_index": None,
            "max_index": None,
            "min_time": None,
            "max_time": None,
        }
    data = values[indices]
    min_pos = int(indices[np.argmin(data)])
    max_pos = int(indices[np.argmax(data)])
    base.update(
        {
            "min": float(values[min_pos]),
            "max": float(values[max_pos]),
            "mean": float(np.mean(data)),
            "rms": float(np.sqrt(np.mean(np.square(data)))),
            "stddev": float(np.std(data)),
            "peak_to_peak": float(np.max(data) - np.min(data)),
            "max_abs": float(np.max(np.abs(data))),
            "min_index": min_pos,
            "max_index": max_pos,
            "min_time": float(time[min_pos]),
            "max_time": float(time[max_pos]),
        }
    )
    return base


def _software_version() -> str:
    try:
        return version("iyzee")
    except PackageNotFoundError:
        return "unknown"


LockLike = AbstractContextManager[object]


@contextlib.contextmanager
def _guarded(scope: object, lock: LockLike | None) -> Iterator[None]:
    """Hold the caller's lock and the driver's lock for a complete batch.

    ``ScopeHandle.lock`` is the same re-entrant lock exposed by the driver as
    ``transaction_lock``; when they are the same object, entering it twice is
    unnecessary. Test doubles without a driver lock still use the explicit
    ``lock=`` argument.
    """
    with contextlib.ExitStack() as stack:
        driver_lock = getattr(scope, "transaction_lock", None)
        if lock is not None:
            stack.enter_context(lock)
        if driver_lock is not None and driver_lock is not lock:
            stack.enter_context(driver_lock)
        yield


def _link_lost(scope: object) -> bool:
    """True once the driver has dropped its connection after a failure.

    The transport invalidates itself when a timeout, disconnect or framing
    error leaves the byte stream untrustworthy. Continuing with the next
    channel would read a late reply as that channel's answer, so batch loops
    stop instead. Scopes without a ``connected`` flag are assumed healthy.
    """
    return getattr(scope, "connected", True) is False


def _not_attempted(channels: Sequence[Channel]) -> list[ChannelError]:
    return [
        ChannelError(channel, ConnectionError("scope connection lost; channel not attempted"))
        for channel in channels
    ]


def _value(raw: str) -> str:
    """The value half of a raw query reply.

    The scope echoes the query's own header back as part of its answer
    (e.g. ``"C1:VOLT_DIV 5.00E-01V"`` for a ``C1:VOLT_DIV?``) — this is
    the last whitespace-separated token, i.e. the part after that echoed
    header.
    """
    return raw.strip().split()[-1]


_VOLTAGE_RE = re.compile(
    r"(?P<number>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)[ \t]*(?P<unit>[fpnumkMGT]?V)?\s*$"
)


def _parse_volts(raw: str) -> float:
    """Parse a LeCroy voltage reply into volts.

    The manuals show both compact responses such as ``5.00E-01V`` and
    responses where the number and unit are separated, such as
    ``200E-3 V``. Accept both forms, plus common SI voltage prefixes, while
    treating a missing unit as volts.
    """
    match = _VOLTAGE_RE.search(raw.strip())
    if match is None:
        raise ValueError(f"could not parse voltage from scope response: {raw!r}")
    scale = {
        "fV": 1e-15,
        "pV": 1e-12,
        "nV": 1e-9,
        "uV": 1e-6,
        "mV": 1e-3,
        "V": 1.0,
        "kV": 1e3,
        "MV": 1e6,
        "GV": 1e9,
    }
    unit = match.group("unit") or "V"
    return float(match.group("number")) * scale[unit]


_SECONDS_RE = re.compile(
    r"(?P<number>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)[ \t]*(?P<unit>[pnumk]?[Ss])?\s*$"
)
_SECONDS_SCALE = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3}


def _parse_seconds(raw: str) -> float:
    """Parse a LeCroy time reply (``TDIV 5.00E-06 S``, ``5E-6S``, ``2 ns``) into seconds."""
    match = _SECONDS_RE.search(raw.strip())
    if match is None:
        raise ValueError(f"could not parse time from scope response: {raw!r}")
    prefix = (match.group("unit") or "S")[:-1]
    return float(match.group("number")) * _SECONDS_SCALE[prefix]


def _trigger_source(raw: str) -> str:
    """Pull the source channel out of a ``TRIG_SELECT?`` reply.

    The reply is comma-separated — trigger type, a qualifier, then the
    source, then further qualifiers (e.g.
    ``"TRIG_SELECT EDGE,SR,C1,HT,OFF"``, matching the
    ``"EDGE,SR,{source}"`` shape :meth:`~iyzee.devices.scope.LeCroy.set_trigger_source`
    itself writes) — so the source is the third comma field of the value.
    """
    fields = _value(raw).split(",")
    if len(fields) < 3:
        raise ValueError(f"unexpected TRIG_SELECT? reply, can't find a source field: {raw!r}")
    return fields[2]


def apply_channel_settings(
    scope: LeCroy,
    settings: Sequence[ChannelSettings],
    *,
    current_settings: Mapping[Channel, ChannelSettings] | Sequence[ChannelSettings] | None = None,
    lock: LockLike | None = None,
) -> list[ChannelError]:
    """Apply channel settings, optionally writing only fields that changed.

    With ``current_settings`` supplied, it is the known baseline returned by
    :func:`read_channel_settings` (or a previously successful apply). Each
    channel is compared field-by-field and only changed values are written.
    This lets a caller edit one field in a complete retrieved snapshot without
    re-sending unrelated settings.

    A channel missing from the supplied baseline is deliberately skipped and
    reported as an error: without a trustworthy baseline, writing its form
    defaults could silently overwrite a setting already on the instrument.
    Omitting ``current_settings`` preserves the original full-write behavior
    for standalone callers that explicitly want to configure every field.

    One channel's failure doesn't stop the rest. The whole batch is held under
    one lock acquisition so another caller cannot interleave a reconfiguration.
    """
    baseline = (
        None
        if current_settings is None
        else dict(current_settings)
        if isinstance(current_settings, Mapping)
        else {s.channel: s for s in current_settings}
    )
    errors: list[ChannelError] = []
    with _guarded(scope, lock):
        for index, desired in enumerate(settings):
            current = baseline.get(desired.channel) if baseline is not None else None
            if baseline is not None and current is None:
                error = RuntimeError(
                    f"{desired.channel} has no retrieved baseline; refusing to overwrite it"
                )
                log.warning("scope: %s", error)
                errors.append(ChannelError(desired.channel, error))
                continue
            try:
                if current is None or desired.volts_per_div != current.volts_per_div:
                    scope.set_volts_per_div(desired.channel, desired.volts_per_div)
                if current is None or desired.offset != current.offset:
                    scope.set_offset(desired.channel, desired.offset)
                if current is None or desired.coupling != current.coupling:
                    scope.set_coupling(desired.channel, desired.coupling)
                if current is None or desired.enabled != current.enabled:
                    scope.set_trace_display(desired.channel, desired.enabled)
            except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                log.exception("scope: failed to apply %s settings", desired.channel)
                errors.append(ChannelError(desired.channel, exc))
                if _link_lost(scope):
                    errors.extend(_not_attempted([s.channel for s in settings[index + 1 :]]))
                    break
    return errors


def apply_and_verify_channel_settings(
    scope: LeCroy,
    settings: Sequence[ChannelSettings],
    *,
    current_settings: Mapping[Channel, ChannelSettings] | Sequence[ChannelSettings] | None = None,
    lock: LockLike | None = None,
    rel_tol: float = 1e-3,
    abs_tol: float = 1e-6,
) -> ChannelApplyResult:
    """Apply channel settings, then read them back and report what the scope holds.

    The scope quantizes vertical scale and offset (0.123 V/div may become
    0.1 V/div), and a command can be silently ignored, so "written" is not
    "applied". After the writes every channel in ``settings`` is read back:

    * ``enabled`` / ``coupling`` are discrete, so a difference is an error.
    * ``volts_per_div`` / ``offset`` differing beyond tolerance are reported as
      :class:`SettingAdjustment` (informational, not a failure).
    * A channel that cannot be read back is an error ("could not verify") and
      is absent from ``verified``.

    The whole write + read-back is one locked batch. If the link is lost the
    read-back is skipped, since a dead stream cannot be trusted.
    """
    with _guarded(scope, lock):
        errors = list(apply_channel_settings(scope, settings, current_settings=current_settings))
        if _link_lost(scope):
            return ChannelApplyResult((), tuple(errors), ())
        attempted = [s.channel for s in settings]
        verified, read_errors = read_channel_settings(scope, attempted)

    errors.extend(
        ChannelError(e.channel, RuntimeError(f"could not verify {e.channel}: {e.error}"))
        for e in read_errors
    )
    desired = {s.channel: s for s in settings}
    adjustments: list[SettingAdjustment] = []
    for actual in verified:
        want = desired[actual.channel]
        if actual.enabled != want.enabled:
            errors.append(
                ChannelError(
                    actual.channel,
                    RuntimeError(
                        f"{actual.channel} trace is {'ON' if actual.enabled else 'OFF'} "
                        f"but {'ON' if want.enabled else 'OFF'} was requested"
                    ),
                )
            )
        if actual.coupling != want.coupling:
            errors.append(
                ChannelError(
                    actual.channel,
                    RuntimeError(
                        f"{actual.channel} coupling is {actual.coupling.value} "
                        f"but {want.coupling.value} was requested"
                    ),
                )
            )
        for name in ("volts_per_div", "offset"):
            requested, got = getattr(want, name), getattr(actual, name)
            if not math.isclose(requested, got, rel_tol=rel_tol, abs_tol=abs_tol):
                adjustments.append(SettingAdjustment(actual.channel, name, requested, got))
    return ChannelApplyResult(tuple(verified), tuple(errors), tuple(adjustments))


def read_channel_settings(
    scope: LeCroy, channels: Sequence[Channel], *, lock: LockLike | None = None
) -> tuple[list[ChannelSettings], list[ChannelError]]:
    """Read every channel's current vertical settings back from the scope.

    The read counterpart of :func:`apply_channel_settings`: same batching
    shape (one lock acquisition for the whole read, one channel's failure
    doesn't stop the rest), same ``ChannelSettings`` shape out as
    ``apply_channel_settings`` takes in — so the values this returns can
    be edited (change one field, leave the rest as read) and passed
    straight back to ``apply_channel_settings`` without reconstructing
    anything by hand.

    Returns ``(settings, errors)``, in the same order as ``channels``
    minus any that failed; a channel present in ``errors`` is simply
    absent from ``settings`` rather than the whole read failing.
    """
    settings: list[ChannelSettings] = []
    errors: list[ChannelError] = []
    with _guarded(scope, lock):
        for index, channel in enumerate(channels):
            try:
                volts_per_div = _parse_volts(scope.get_volts_per_div(channel))
                offset = _parse_volts(scope.get_offset(channel))
                coupling = Coupling(_value(scope.get_coupling(channel)))
                enabled = _value(scope.get_trace_display(channel)) == "ON"
                settings.append(ChannelSettings(channel, enabled, volts_per_div, offset, coupling))
            except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                log.exception("scope: failed to read %s settings", channel)
                errors.append(ChannelError(channel, exc))
                if _link_lost(scope):
                    errors.extend(_not_attempted(channels[index + 1 :]))
                    break
    return settings, errors


def apply_trigger_settings(
    scope: LeCroy,
    settings: TriggerSettings,
    *,
    current_settings: TriggerSettings | None = None,
    lock: LockLike | None = None,
) -> None:
    """Apply trigger settings, optionally writing only fields that changed.

    With a retrieved ``current_settings`` baseline, unchanged fields are left
    alone. If the trigger source changes, only source-specific fields that
    also differ from the old baseline are written on the new source; the new
    source's existing slope/coupling/level are otherwise preserved. This
    avoids copying the old source's configuration onto the newly selected
    source by accident.

    Omitting ``current_settings`` keeps the original behavior: write the full
    trigger configuration supplied by the caller. The mode is always written
    last, so an arming mode never fires against a half-updated configuration. Failures still raise because
    there is only one trigger configuration to report.
    """
    with _guarded(scope, lock):
        if current_settings is None:
            scope.set_trigger_source(settings.source)
            scope.set_trigger_slope(settings.source, settings.slope)
            scope.set_trigger_coupling(settings.source, settings.coupling)
            scope.set_trigger_level(settings.source, settings.level_volts)
            if settings.time_per_div is not None:
                scope.set_time_per_div(settings.time_per_div)
            scope.set_trigger_mode(settings.mode)
            return

        if settings.source != current_settings.source:
            scope.set_trigger_source(settings.source)
        if settings.slope != current_settings.slope:
            scope.set_trigger_slope(settings.source, settings.slope)
        if settings.coupling != current_settings.coupling:
            scope.set_trigger_coupling(settings.source, settings.coupling)
        if settings.level_volts != current_settings.level_volts:
            scope.set_trigger_level(settings.source, settings.level_volts)
        if (
            settings.time_per_div is not None
            and settings.time_per_div != current_settings.time_per_div
        ):
            scope.set_time_per_div(settings.time_per_div)
        # Mode last: switching to NORMAL/SINGLE/AUTO arms an acquisition, and it
        # should arm against the new source/level, not a half-written config.
        if settings.mode != current_settings.mode:
            scope.set_trigger_mode(settings.mode)


def read_trigger_settings(scope: LeCroy, *, lock: LockLike | None = None) -> TriggerSettings:
    """Read the scope's current trigger configuration back.

    The read counterpart of :func:`apply_trigger_settings`, including its
    error handling: raises on failure rather than collecting errors —
    there's one trigger, not a batch, so there's nothing to partially
    read.
    """
    with _guarded(scope, lock):
        source = Channel(_trigger_source(scope.get_trigger_source()))
        mode = TriggerMode(_value(scope.get_trigger_mode()))
        slope = TriggerSlope(_value(scope.get_trigger_slope(source)))
        coupling = TriggerCoupling(_value(scope.get_trigger_coupling(source)))
        level_volts = _parse_volts(scope.get_trigger_level(source))
        time_per_div = _parse_seconds(scope.get_time_per_div())
    return TriggerSettings(source, mode, slope, coupling, level_volts, time_per_div)


def _download_waveform(scope: LeCroy, channel: Channel) -> ScopeWaveform:
    """Download one channel's ``DAT1`` block with its own timebase (caller holds the lock)."""
    time_unit, time_offset, time_interval = scope.getHorProperties(channel=channel)
    data = scope.getDataFloatsDetailed(channel=channel, block="DAT1")
    value_unit = str(data["unit"])
    values = np.asarray(data["values"], dtype=np.float64)
    raw_codes = np.asarray(data["raw_codes"], dtype=np.int16)
    vertical_gain = float(data["vertical_gain"])
    vertical_offset = float(data["vertical_offset"])
    if values.size == 0:
        raise ValueError(f"{channel} returned an empty waveform")
    time_values = time_offset + np.arange(values.size, dtype=np.float64) * time_interval
    return ScopeWaveform(
        channel=channel,
        time=time_values,
        values=values,
        raw_codes=raw_codes,
        value_unit=value_unit,
        time_unit=str(time_unit),
        time_offset=float(time_offset),
        time_interval=float(time_interval),
        vertical_gain=vertical_gain,
        vertical_offset=vertical_offset,
        stats=_stats(time_values, values),
    )


def acquire_scope_recording(
    scope: LeCroy,
    channels: Sequence[Channel],
    *,
    channel_settings: Sequence[ChannelSettings] = (),
    trigger_settings: TriggerSettings | None = None,
    applied_channel_settings: Sequence[ChannelSettings] | None = None,
    applied_trigger_settings: TriggerSettings | None = None,
    lock: LockLike | None = None,
    freeze: bool = True,
) -> ScopeAcquisition:
    """Acquire ``DAT1`` waveforms and capture their interpretation metadata.

    The returned object contains per-channel time axes, calibrated values,
    optional raw signed 16-bit samples, scope-reported calibration data, and
    descriptive statistics. It also carries the requested configuration and
    the last configuration known to have been applied. This function does
    not write to disk; :func:`save_scope_acquisition` owns persistence.

    Consistency: each channel's own timebase is read alongside its data (a
    math trace or a different record length need not share channel 1's).
    Channels are downloaded one after another, so with ``freeze=True`` (the
    default) a *running* acquisition (trigger mode AUTO or NORMAL) is stopped
    for the download and the previous mode restored afterwards; otherwise
    each channel could come from a different capture. SINGLE and STOP hold
    still already and are left alone. If freezing or restoring fails, the
    recording still succeeds and says so in ``warnings``.

    Acquisition is deliberately tolerant of per-channel failures: a channel
    that cannot be downloaded is recorded as an error while other channels
    are retained, unless the connection itself was lost, in which case the
    remaining channels are reported as not attempted.
    """
    if not channels:
        raise ValueError("at least one channel is required")
    started = utc_now()
    measurement_id = uuid.uuid4().hex
    errors: list[ChannelError] = []
    waveforms: list[ScopeWaveform] = []
    warnings: list[str] = []
    instrument_id: str | None = None
    prior_mode: TriggerMode | None = None
    frozen = False

    with _guarded(scope, lock):
        query = getattr(scope, "query", None)
        if callable(query):
            try:
                instrument_id = str(query("*IDN?"))
            except Exception as exc:  # noqa: BLE001 - provenance is best-effort
                log.warning("scope: could not read *IDN?: %s", exc)
                warnings.append(f"instrument identity unavailable: {exc}")

        if freeze and not _link_lost(scope):
            try:
                prior_mode = TriggerMode(_value(scope.get_trigger_mode()))
                if prior_mode in (TriggerMode.AUTO, TriggerMode.NORMAL):
                    scope.set_trigger_mode(TriggerMode.STOP)
                    frozen = True
            except Exception as exc:  # noqa: BLE001
                log.warning("scope: could not freeze acquisition: %s", exc)
                warnings.append(f"could not freeze acquisition; channels may differ: {exc}")

        try:
            for index, channel in enumerate(channels):
                try:
                    waveforms.append(_download_waveform(scope, channel))
                except Exception as exc:  # noqa: BLE001
                    log.exception("scope: failed to acquire %s", channel)
                    errors.append(ChannelError(channel, exc))
                    if _link_lost(scope):
                        errors.extend(_not_attempted(channels[index + 1 :]))
                        break
        finally:
            if frozen and prior_mode is not None and not _link_lost(scope):
                try:
                    scope.set_trigger_mode(prior_mode)
                except Exception as exc:  # noqa: BLE001
                    log.exception("scope: could not restore trigger mode %s", prior_mode)
                    warnings.append(
                        f"could not restore trigger mode {prior_mode.value}; scope is left "
                        f"in {TriggerMode.STOP.value}: {exc}"
                    )
            elif frozen:
                warnings.append(
                    f"connection lost while frozen; trigger mode {prior_mode} not restored"
                )

    return ScopeAcquisition(
        measurement_id=measurement_id,
        started_at_utc=started,
        completed_at_utc=utc_now(),
        instrument_address=getattr(scope, "address", None),
        socket_timeout_s=getattr(scope, "SOCK_TIMEOUT", None),
        requested_channel_settings=tuple(channel_settings),
        requested_trigger_settings=trigger_settings,
        applied_channel_settings=(
            tuple(applied_channel_settings) if applied_channel_settings is not None else None
        ),
        applied_trigger_settings=applied_trigger_settings,
        waveforms=tuple(waveforms),
        errors=tuple(errors),
        instrument_id=instrument_id,
        frozen=frozen,
        prior_trigger_mode=prior_mode,
        warnings=tuple(warnings),
    )


def save_scope_acquisition(
    recording: ScopeAcquisition,
    savedir: Path,
    *,
    name: str = "scope",
    path: Path | None = None,
) -> Path:
    """Persist one scope acquisition as numeric NPZ arrays plus a JSON manifest.

    Each successful channel contributes ``time_<channel>`` and
    ``value_<channel>`` arrays, plus ``raw_<channel>`` when raw ADC codes were
    retained. The manifest records acquisition identity/timing, instrument
    transport details, requested vs. successfully-applied configuration,
    calibration/timebase metadata, descriptive statistics, and per-channel
    errors. Persistence itself is delegated to the shared numeric-recording
    primitive in :mod:`iyzee.experiment.io`.
    """
    arrays: dict[str, np.ndarray] = {}
    channel_metadata: list[dict[str, object]] = []
    for waveform in recording.waveforms:
        key = str(waveform.channel)
        arrays[f"time_{key}"] = np.asarray(waveform.time, dtype=np.float64)
        arrays[f"value_{key}"] = np.asarray(waveform.values, dtype=np.float64)
        if waveform.raw_codes is not None:
            arrays[f"raw_{key}"] = np.asarray(waveform.raw_codes, dtype=np.int16)
        channel_metadata.append(
            {
                "channel": key,
                "value_unit": waveform.value_unit,
                "time_unit": waveform.time_unit,
                "time_offset": waveform.time_offset,
                "time_interval": waveform.time_interval,
                "vertical_gain": waveform.vertical_gain,
                "vertical_offset": waveform.vertical_offset,
                "stats": waveform.stats,
                "raw_array": f"raw_{key}" if waveform.raw_codes is not None else None,
            }
        )

    def channel_config(s: ChannelSettings) -> dict[str, object]:
        return {
            "channel": s.channel.value,
            "enabled": s.enabled,
            "volts_per_div": s.volts_per_div,
            "offset": s.offset,
            "coupling": s.coupling.value,
        }

    def trigger_config(s: TriggerSettings | None) -> dict[str, object] | None:
        if s is None:
            return None
        return {
            "source": s.source.value,
            "mode": s.mode.value,
            "slope": s.slope.value,
            "coupling": s.coupling.value,
            "level_volts": s.level_volts,
            "time_per_div": s.time_per_div,
        }

    metadata = {
        "kind": "scope-acquisition",
        "schema_version": 2,
        "measurement_id": recording.measurement_id,
        "started_at_utc": recording.started_at_utc,
        "completed_at_utc": recording.completed_at_utc,
        "software": {
            "name": "iyzee",
            "version": _software_version(),
            "python": platform.python_version(),
        },
        "instrument": {
            "driver": "iyzee.devices.scope.LeCroy",
            "protocol": "LeCroy VICP",
            "address": recording.instrument_address,
            "identity": recording.instrument_id,
            "port": LeCroy.LECROY_SERVER_PORT,
            "socket_timeout_s": recording.socket_timeout_s,
        },
        "configuration": {
            "requested_channel_settings": [
                channel_config(s) for s in recording.requested_channel_settings
            ],
            "requested_trigger_settings": trigger_config(recording.requested_trigger_settings),
            "applied_channel_settings": (
                [channel_config(s) for s in recording.applied_channel_settings]
                if recording.applied_channel_settings is not None
                else None
            ),
            "applied_trigger_settings": trigger_config(recording.applied_trigger_settings),
        },
        "acquisition": {
            "frozen": recording.frozen,
            "prior_trigger_mode": (
                recording.prior_trigger_mode.value if recording.prior_trigger_mode else None
            ),
            "warnings": list(recording.warnings),
        },
        "waveforms": channel_metadata,
        "errors": [
            {"channel": e.channel.value, "type": type(e.error).__name__, "error": str(e.error)}
            for e in recording.errors
        ],
        "data_semantics": {
            "value_arrays": "engineering units using the scope-reported VERTICAL_GAIN and VERTICAL_OFFSET",
            "raw_arrays": "signed 16-bit waveform codes returned by the scope when available",
            "time_arrays": "scope horizontal offset plus sample index multiplied by horizontal interval",
        },
    }
    return save_numeric_recording(arrays, savedir, metadata, name=name, path=path)
