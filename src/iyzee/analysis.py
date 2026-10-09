"""Read what a saved recording says, without a UI: sweep and scope summaries.

``experiment.io.load_recording`` returns the raw arrays and the JSON manifest;
this module turns them into typed answers a script, the IPython console and the
Results screen can all share. It imports no Textual and no driver, so the screen
decides how a summary is *worded and drawn* while this module decides what it
*means*.

Vocabulary used here (and by the Results screen):

* **recording**: one saved ``.npz``/``.json`` pair, either a sweep or a scope
  acquisition (``metadata["kind"]``).
* **point**: one position in a sweep. Running code calls the same thing a
  *step* (``StepResult``); a saved file only ever has points.
* **requested / measured**: the setpoint asked for versus what the instrument
  reported (the wavemeter's frequency after settling).
* **relative noise**: the squeezing trace minus the shot-noise trace, in dB, so a
  negative value is noise *below* shot noise. It is reduced to one number per
  point by a **statistic** (``"mean"`` or ``"minimum"`` over the trace).
  "Delta" is deliberately not used for this: in the physics chapter Δ is the
  detuning from an atomic line.

Guide: :guide:`data-sweep`
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from .experiment.io import Recording, difference_values_many
from .waveform_math import Trace, traces_from_scope_recording

Statistic = Literal["mean", "minimum"]
STATISTICS: tuple[Statistic, ...] = ("mean", "minimum")

SweepAxis = Literal["frequency", "bandwidth"]


def _finite(value: Any) -> float | None:
    """``value`` as a finite float, or ``None`` for anything missing or non-finite."""
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    """The dict entries of a manifest list; anything else (missing, hand-edited) is dropped."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# --- Sweep recordings -------------------------------------------------------


@dataclass(frozen=True)
class SweepPoint:
    """One point of a saved sweep."""

    index: int
    label: str
    requested: float
    """The setpoint: RBW in Hz for a bandwidth sweep, frequency in THz for a frequency sweep."""
    measured: float | None
    """The wavemeter reading in THz after settling; ``None`` when there is none (always for bandwidth)."""
    relative_noise_db: float | None
    """Noise relative to shot noise in dB; ``None`` when the point has no usable trace pair."""

    @property
    def x(self) -> float:
        """Where to plot this point: the measured value when there is one, else the setpoint."""
        return self.measured if self.measured is not None else self.requested


@dataclass(frozen=True)
class SweepSummary:
    """A saved sweep as one object per point, with the statistic that was applied."""

    axis: SweepAxis
    statistic: Statistic
    points: tuple[SweepPoint, ...]

    @property
    def x_unit(self) -> str:
        return "THz" if self.axis == "frequency" else "Hz"


def _is_frequency_sweep(points: Sequence[dict[str, Any]]) -> bool:
    return any(
        "wavemeter_channel" in point or "measured_frequency_thz" in point for point in points
    )


def summarize_sweep(recording: Recording, statistic: Statistic = "mean") -> SweepSummary:
    """Interpret a loaded sweep recording.

    Tolerates the same partial recordings the Results screen always has: a
    missing sidecar, fewer manifest points than rows, or a missing trace each
    leave that point's field empty (a default label, ``None``) rather than
    raising. A recording with no ``x_values`` array has no points.

    Raises ``ValueError`` for a ``statistic`` that is not one of :data:`STATISTICS`.
    """
    arrays = recording.arrays
    x_values = np.asarray(arrays.get("x_values", []), dtype=np.float64)
    manifest_points = recording.metadata.get("points", [])
    if not isinstance(manifest_points, list):
        manifest_points = []
    manifest_points = [point if isinstance(point, dict) else {} for point in manifest_points]
    rows = len(x_values)

    squeezing = arrays.get("trace_squeezing")
    shot_noise = arrays.get("trace_shot_noise")
    values = difference_values_many(
        squeezing if squeezing is not None else [None] * rows,
        shot_noise if shot_noise is not None else [None] * rows,
        statistic,
    )

    axis: SweepAxis = "frequency" if _is_frequency_sweep(manifest_points) else "bandwidth"
    points = []
    for index in range(rows):
        manifest = manifest_points[index] if index < len(manifest_points) else {}
        measured = _finite(manifest.get("measured_frequency_thz")) if axis == "frequency" else None
        points.append(
            SweepPoint(
                index=index,
                label=str(manifest.get("label") or f"point {index}"),
                requested=float(x_values[index]),
                measured=measured,
                relative_noise_db=values[index],
            )
        )
    return SweepSummary(axis=axis, statistic=statistic, points=tuple(points))


# --- Scope recordings -------------------------------------------------------


@dataclass(frozen=True)
class ScopeChannelStats:
    """The per-channel statistics the acquisition stored; ``None`` where the manifest lacks one."""

    channel: str
    value_unit: str
    sample_count: int | None
    minimum: float | None
    maximum: float | None
    peak_to_peak: float | None
    rms: float | None


@dataclass(frozen=True)
class ScopeTimebase:
    """The horizontal setup shared by one or more channels of a scope recording."""

    channels: tuple[str, ...]
    offset: float
    """Time of the first sample relative to the trigger; negative means pre-trigger data."""
    interval: float
    samples: int
    unit: str


@dataclass(frozen=True)
class ScopeSummary:
    """What a scope recording says about itself, ready for any renderer to word."""

    measurement_id: str | None
    started_at_utc: str | None
    instrument_address: str | None
    """``None`` when the manifest has no instrument section; ``""`` when it has one with no address."""
    channels: tuple[ScopeChannelStats, ...]
    time_per_div: float | None
    timebases: tuple[ScopeTimebase, ...]
    errors: Any
    """The manifest's ``errors`` entry exactly as stored (falsy when the acquisition had none)."""


def _number(value: Any) -> float | None:
    """A real number from the manifest; ``bool`` and strings are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _time_per_div(metadata: dict[str, Any]) -> float | None:
    config = metadata.get("configuration")
    if not isinstance(config, dict):
        return None
    trigger = config.get("applied_trigger_settings") or config.get("requested_trigger_settings")
    return _number(trigger.get("time_per_div")) if isinstance(trigger, dict) else None


def _timebases(waveforms: list[dict[str, Any]]) -> tuple[ScopeTimebase, ...]:
    """Group channels that share a timebase; groups whose numbers are unusable are left out."""
    groups: dict[tuple[Any, Any, Any, Any], list[str]] = {}
    for waveform in waveforms:
        stats = waveform.get("stats")
        count = stats.get("sample_count") if isinstance(stats, dict) else None
        key = (
            waveform.get("time_offset"),
            waveform.get("time_interval"),
            count,
            waveform.get("time_unit", "S"),
        )
        groups.setdefault(key, []).append(str(waveform.get("channel")))

    timebases = []
    for (offset, interval, count, unit), channels in groups.items():
        if not (isinstance(offset, int | float) and isinstance(interval, int | float)):
            continue
        if not (isinstance(count, int) and count > 0 and interval > 0):
            continue
        timebases.append(ScopeTimebase(tuple(channels), offset, interval, count, str(unit)))
    return tuple(timebases)


def summarize_scope(recording: Recording, traces: Sequence[Trace] | None = None) -> ScopeSummary:
    """Interpret a loaded scope-acquisition recording.

    ``traces`` are the channels that actually have data; pass the result of
    :func:`~iyzee.waveform_math.traces_from_scope_recording` if you already have
    it, otherwise it is rebuilt. Channel statistics are reported for those
    channels only, in the same order.
    """
    metadata = recording.metadata
    if traces is None:
        traces = traces_from_scope_recording(recording)
    waveforms = _dicts(metadata.get("waveforms"))

    stats_by_channel = {
        str(waveform.get("channel")): waveform.get("stats")
        for waveform in waveforms
        if isinstance(waveform.get("stats"), dict)
    }
    channels = []
    for trace in traces:
        stats = stats_by_channel.get(trace.label)
        if stats is None:
            continue
        count = stats.get("sample_count")
        channels.append(
            ScopeChannelStats(
                channel=trace.label,
                value_unit=trace.value_unit,
                sample_count=count if isinstance(count, int) else None,
                minimum=_number(stats.get("min")),
                maximum=_number(stats.get("max")),
                peak_to_peak=_number(stats.get("peak_to_peak")),
                rms=_number(stats.get("rms")),
            )
        )

    instrument = metadata.get("instrument")
    address = str(instrument.get("address") or "") if isinstance(instrument, dict) else None
    measurement_id = metadata.get("measurement_id")
    started_at = metadata.get("started_at_utc")
    return ScopeSummary(
        measurement_id=str(measurement_id) if measurement_id else None,
        started_at_utc=str(started_at) if started_at else None,
        instrument_address=address,
        channels=tuple(channels),
        time_per_div=_time_per_div(metadata),
        timebases=_timebases(waveforms),
        errors=metadata.get("errors"),
    )
