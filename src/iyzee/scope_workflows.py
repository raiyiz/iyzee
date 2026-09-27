"""Scope workflow operations: what "apply these channel settings" or
"acquire every enabled channel's waveform" means on top of the raw
:class:`~iyzee.scope.LeCroy` driver.

Plain functions and dataclasses, no Textual import — the same operations
``ScopeScreen``'s buttons trigger are usable identically from a script or
the IPython console::

    from iyzee.scope import Channel, Coupling, LeCroy
    from iyzee.scope_workflows import ChannelSettings, apply_channel_settings

    scope = LeCroy()
    scope.connect("10.0.0.5")
    apply_channel_settings(scope, [ChannelSettings(Channel.C1, True, 0.5, 0.0, Coupling.DC_1M)])

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
import threading
import uuid
from collections.abc import Sequence
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
    """One channel's operation failed; carries which channel and why —
    used by both :func:`apply_channel_settings` (a channel whose settings
    couldn't be pushed) and :func:`acquire_waveforms` (a channel whose
    waveform couldn't be downloaded)."""

    channel: Channel
    error: Exception



@dataclass(frozen=True)
class ScopeWaveform:
    """One channel's calibrated waveform and the scope's conversion data."""

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
    """A complete scope acquisition suitable for persistent scientific recording."""

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


def _locked(lock: threading.Lock | None) -> AbstractContextManager[object]:
    """``lock`` if given, else a no-op context — so a script with its own
    private ``LeCroy`` never needs to construct a throwaway lock just to
    call these functions."""
    return lock if lock is not None else contextlib.nullcontext()


def apply_channel_settings(
    scope: LeCroy, settings: Sequence[ChannelSettings], *, lock: threading.Lock | None = None
) -> list[ChannelError]:
    """Push every channel's vertical settings to the scope.

    One channel's failure doesn't stop the rest — a typo in channel 3's
    coupling shouldn't also block channel 1 from getting its volts/div.
    Returns the list of failures (empty if everything succeeded) rather
    than raising, so a caller can decide how to report a partial failure
    instead of losing the channels that *did* apply.

    Held under one lock acquisition for the whole batch, not once per
    channel: this is one logical "reconfigure the scope" operation, and a
    console command interleaving partway through it would leave the scope
    in a mixed state just as broken as two callers reconfiguring it at
    once.
    """
    errors: list[ChannelError] = []
    with _locked(lock):
        for s in settings:
            try:
                scope.set_volts_per_div(s.channel, s.volts_per_div)
                scope.set_offset(s.channel, s.offset)
                scope.set_coupling(s.channel, s.coupling)
                scope.set_trace_display(s.channel, s.enabled)
            except Exception as exc:  # noqa: BLE001 - collected, not swallowed
                log.exception("scope: failed to apply %s settings", s.channel)
                errors.append(ChannelError(s.channel, exc))
    return errors


def apply_trigger_settings(
    scope: LeCroy, settings: TriggerSettings, *, lock: threading.Lock | None = None
) -> None:
    """Push the trigger configuration to the scope.

    Raises on failure, unlike :func:`apply_channel_settings` — there's
    only one trigger, so unlike a batch of channels there's nothing that
    could partially succeed; a caller just needs to know it worked or it
    didn't.
    """
    with _locked(lock):
        scope.set_trigger_mode(settings.mode)
        scope.set_trigger_source(settings.source)
        scope.set_trigger_slope(settings.source, settings.slope)
        scope.set_trigger_coupling(settings.source, settings.coupling)
        scope.set_trigger_level(settings.source, settings.level_volts)



def acquire_scope_recording(
    scope: LeCroy,
    channels: Sequence[Channel],
    *,
    channel_settings: Sequence[ChannelSettings] = (),
    trigger_settings: TriggerSettings | None = None,
    applied_channel_settings: Sequence[ChannelSettings] | None = None,
    applied_trigger_settings: TriggerSettings | None = None,
    lock: threading.Lock | None = None,
) -> ScopeAcquisition:
    """Acquire complete waveforms and the metadata needed to interpret them."""
    if not channels:
        raise ValueError("at least one channel is required")
    started = _utc_now()
    measurement_id = uuid.uuid4().hex
    errors: list[ChannelError] = []
    waveforms: list[ScopeWaveform] = []
    with _locked(lock):
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
        for channel in channels:
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
    scope: LeCroy, channels: Sequence[Channel], *, lock: threading.Lock | None = None
) -> tuple[list[tuple[list[float], list[float], str]], list[ChannelError]]:
    """Compatibility/presentation wrapper over :func:`acquire_scope_recording`."""
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
    """Save one scope acquisition as numeric NPZ arrays plus a JSON manifest."""
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
