"""Scope workflow operations: configuration, acquisition, and persistence
on top of the raw :class:`~iyzee.scope.LeCroy` driver.

Plain functions and dataclasses, no Textual import — the same operations
``ScopeScreen``'s buttons trigger are usable identically from a script or
the IPython console::

    from iyzee.scope import Channel, Coupling, LeCroy
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
"""

from __future__ import annotations

import contextlib
import logging
import platform
import re
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from .experiment.io import save_numeric_recording
from .scope import Channel, Coupling, LeCroy, TriggerCoupling, TriggerMode, TriggerSlope

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
    """Hold ``lock`` (if given) and the driver's own transaction for a batch.

    Entering ``scope.transaction()`` means a batch is atomic against any other
    thread using the driver, even one that never heard of ``lock``. For a
    ``ScopeHandle`` both are the same re-entrant lock, so passing the handle's
    lock is redundant but harmless (and cannot deadlock through a
    ``LockedProxy``). Scopes without ``transaction()`` (test fakes) just use
    ``lock``.
    """
    with contextlib.ExitStack() as stack:
        if lock is not None:
            stack.enter_context(lock)
        transaction = getattr(scope, "transaction", None)
        if callable(transaction):
            stack.enter_context(transaction())
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


def _trigger_source(raw: str) -> str:
    """Pull the source channel out of a ``TRIG_SELECT?`` reply.

    The reply is comma-separated — trigger type, a qualifier, then the
    source, then further qualifiers (e.g.
    ``"TRIG_SELECT EDGE,SR,C1,HT,OFF"``, matching the
    ``"EDGE,SR,{source}"`` shape :meth:`~iyzee.scope.LeCroy.set_trigger_source`
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
    trigger configuration supplied by the caller. Failures still raise because
    there is only one trigger configuration to report.
    """
    with _guarded(scope, lock):
        if current_settings is None:
            scope.set_trigger_mode(settings.mode)
            scope.set_trigger_source(settings.source)
            scope.set_trigger_slope(settings.source, settings.slope)
            scope.set_trigger_coupling(settings.source, settings.coupling)
            scope.set_trigger_level(settings.source, settings.level_volts)
            return

        if settings.mode != current_settings.mode:
            scope.set_trigger_mode(settings.mode)
        if settings.source != current_settings.source:
            scope.set_trigger_source(settings.source)
        if settings.slope != current_settings.slope:
            scope.set_trigger_slope(settings.source, settings.slope)
        if settings.coupling != current_settings.coupling:
            scope.set_trigger_coupling(settings.source, settings.coupling)
        if settings.level_volts != current_settings.level_volts:
            scope.set_trigger_level(settings.source, settings.level_volts)


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
    return TriggerSettings(source, mode, slope, coupling, level_volts)


def acquire_scope_recording(
    scope: LeCroy,
    channels: Sequence[Channel],
    *,
    channel_settings: Sequence[ChannelSettings] = (),
    trigger_settings: TriggerSettings | None = None,
    applied_channel_settings: Sequence[ChannelSettings] | None = None,
    applied_trigger_settings: TriggerSettings | None = None,
    lock: LockLike | None = None,
) -> ScopeAcquisition:
    """Acquire enabled ``DAT1`` waveforms and capture their interpretation metadata.

    The returned object contains per-channel time axes, calibrated values,
    optional raw signed 16-bit samples, scope-reported calibration data, and
    descriptive statistics. It also carries the requested configuration and
    the last configuration known to have been applied. This function does
    not write to disk; :func:`save_scope_acquisition` owns persistence.

    Acquisition is deliberately tolerant of per-channel failures: a channel
    that cannot be downloaded is recorded as an error while other channels
    are retained."""
    if not channels:
        raise ValueError("at least one channel is required")
    started = _utc_now()
    measurement_id = uuid.uuid4().hex
    errors: list[ChannelError] = []
    waveforms: list[ScopeWaveform] = []
    with _guarded(scope, lock):
        try:
            time_unit, time_offset, time_interval = scope.getHorProperties(channel=channels[0])
        except Exception as exc:  # noqa: BLE001
            log.exception("scope: failed to read the timebase from %s", channels[0])
            errors.extend(ChannelError(channel, exc) for channel in channels)
            return ScopeAcquisition(
                measurement_id=measurement_id,
                started_at_utc=started,
                completed_at_utc=_utc_now(),
                instrument_address=getattr(scope, "address", None),
                socket_timeout_s=getattr(scope, "SOCK_TIMEOUT", None),
                requested_channel_settings=tuple(channel_settings),
                requested_trigger_settings=trigger_settings,
                applied_channel_settings=(
                    tuple(applied_channel_settings)
                    if applied_channel_settings is not None
                    else None
                ),
                applied_trigger_settings=applied_trigger_settings,
                waveforms=(),
                errors=tuple(errors),
            )
        for index, channel in enumerate(channels):
            try:
                detailed = getattr(scope, "getDataFloatsDetailed", None)
                if callable(detailed):
                    data = detailed(channel=channel, block="DAT1")
                    value_unit = str(data["unit"])
                    values = np.asarray(data["values"], dtype=np.float64)
                    raw_codes = np.asarray(data["raw_codes"], dtype=np.int16)
                    vertical_gain = float(data["vertical_gain"])
                    vertical_offset = float(data["vertical_offset"])
                else:
                    value_unit, values_raw = scope.getDataFloats(channel=channel, block="DAT1")
                    values = np.asarray(values_raw, dtype=np.float64)
                    raw_codes = None
                    vertical_gain = None
                    vertical_offset = None
                time_values = time_offset + np.arange(values.size, dtype=np.float64) * time_interval
                waveforms.append(
                    ScopeWaveform(
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
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("scope: failed to acquire %s", channel)
                errors.append(ChannelError(channel, exc))
                if _link_lost(scope):
                    errors.extend(_not_attempted(channels[index + 1 :]))
                    break
    return ScopeAcquisition(
        measurement_id=measurement_id,
        started_at_utc=started,
        completed_at_utc=_utc_now(),
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
    )


def acquire_waveforms(
    scope: LeCroy, channels: Sequence[Channel], *, lock: LockLike | None = None
) -> tuple[list[tuple[list[float], list[float], str]], list[ChannelError]]:
    """Return the legacy plot-series shape without adding persistence.

    New callers that need a durable scientific record should use
    :func:`acquire_scope_recording` followed by :func:`save_scope_acquisition`.
    This wrapper remains for existing presentation/script callers that only
    need ``(x, y, label)`` series."""
    recording = acquire_scope_recording(scope, channels, lock=lock)
    series = [
        (waveform.time.tolist(), waveform.values.tolist(), str(waveform.channel))
        for waveform in recording.waveforms
    ]
    return series, list(recording.errors)


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
        }

    metadata = {
        "kind": "scope-acquisition",
        "schema_version": 1,
        "measurement_id": recording.measurement_id,
        "started_at_utc": recording.started_at_utc,
        "completed_at_utc": recording.completed_at_utc,
        "software": {
            "name": "iyzee",
            "version": _software_version(),
            "python": platform.python_version(),
        },
        "instrument": {
            "driver": "iyzee.scope.LeCroy",
            "protocol": "LeCroy VICP",
            "address": recording.instrument_address,
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
        "waveforms": channel_metadata,
        "errors": [{"channel": e.channel.value, "error": str(e.error)} for e in recording.errors],
        "data_semantics": {
            "value_arrays": "engineering units using the scope-reported VERTICAL_GAIN and VERTICAL_OFFSET",
            "raw_arrays": "signed 16-bit waveform codes returned by the scope when available",
            "time_arrays": "scope horizontal offset plus sample index multiplied by horizontal interval",
        },
    }
    return save_numeric_recording(arrays, savedir, metadata, name=name, path=path)
