"""Post-processing for saved oscilloscope waveforms.

Kept beside ``scope.py``/``scope_workflows.py`` rather than inside
``experiment/`` because it is oscilloscope/waveform-specific, the same way
``experiment/`` is sweep-specific — not because the two families of data are
meant to diverge in every other way. Both save through
``experiment.io.save_numeric_recording``, and both ultimately produce line
data a renderer can draw: ``experiment.io.difference_series``/
``build_figure`` for a sweep, :func:`traces_from_scope_recording`/
:func:`build_waveform_figure` here for a scope acquisition.

Every function below is pure — a :class:`Trace` in, a new ``Trace`` out, no
``Channel`` enum, socket, or Textual import — so the same call works
identically from a script, the IPython console, or the Results screen. Loading
a saved run happens one layer down, in ``experiment.io.load_recording``; this
module only interprets the loaded arrays/metadata once they're a
:class:`~iyzee.experiment.io.Recording`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib.pyplot as plt
import numpy as np

if TYPE_CHECKING:
    from matplotlib.figure import Figure

    from .experiment.io import Recording


@dataclass(frozen=True)
class Trace:
    """One x/y series ready to plot or feed into another operation.

    Deliberately the same shape whether it came straight off a saved
    channel (see :func:`traces_from_scope_recording`) or is the result of
    combining/adjusting existing traces (:func:`subtract_traces` and
    friends) — every function in this module both takes and returns this
    type, so operations compose without special-casing which kind of
    ``Trace`` they're given.
    """

    label: str
    time: np.ndarray
    values: np.ndarray
    time_unit: str
    value_unit: str
    meta: dict[str, Any] = field(default_factory=dict)
    """Freeform provenance shown in a plot legend (channel, V/div, coupling,
    how a derived trace was built) — display-only. The math operations below
    never read it back; they only ever set their own ``"source"`` entry on
    what they return."""


def traces_from_scope_recording(recording: Recording) -> list[Trace]:
    """Rebuild each channel's :class:`Trace` from a loaded scope-acquisition recording.

    Reads exactly the arrays/metadata ``scope_workflows.save_scope_acquisition``
    writes: ``time_<channel>``/``value_<channel>`` arrays, plus the
    ``waveforms`` and ``configuration.applied_channel_settings`` manifest
    sections for units and legend metadata. A channel listed in the manifest
    but missing either array is skipped rather than raising — the same
    tolerance ``ResultsScreen`` already has for a partial or hand-edited
    recording, and the reason this returns whatever channels *are* usable
    instead of an all-or-nothing result.

    Returns ``[]``, not an error, for a recording that isn't a scope
    acquisition at all (no ``"waveforms"`` key) — callers that care should
    check ``recording.metadata.get("kind")`` themselves rather than this
    function raising on their behalf.
    """
    metadata = recording.metadata
    applied = {
        str(settings.get("channel")): settings
        for settings in (metadata.get("configuration") or {}).get("applied_channel_settings") or []
    }
    traces: list[Trace] = []
    for waveform in metadata.get("waveforms", []):
        if not isinstance(waveform, dict):
            continue
        channel = str(waveform.get("channel", "?"))
        time_array = recording.arrays.get(f"time_{channel}")
        value_array = recording.arrays.get(f"value_{channel}")
        if time_array is None or value_array is None:
            continue
        settings = applied.get(channel, {})
        meta: dict[str, Any] = {"channel": channel, "source": channel}
        if "volts_per_div" in settings:
            meta["volts_per_div"] = settings["volts_per_div"]
        if "coupling" in settings:
            meta["coupling"] = settings["coupling"]
        traces.append(
            Trace(
                label=channel,
                time=np.asarray(time_array, dtype=np.float64),
                values=np.asarray(value_array, dtype=np.float64),
                time_unit=str(waveform.get("time_unit", "")),
                value_unit=str(waveform.get("value_unit", "")),
                meta=meta,
            )
        )
    return traces


def _resample_onto(reference: Trace, other: Trace) -> np.ndarray:
    """``other.values`` interpolated onto ``reference.time``.

    ``np.interp`` (flat-extrapolated past either end — the ordinary
    convention for aligning two waveforms that don't share a sample grid,
    e.g. differing time offsets or intervals). Skipped — returns
    ``other.values`` unchanged — when the grids already match exactly, so
    two channels from the same acquisition are combined sample-for-sample
    rather than smoothed by needless interpolation.
    """
    if reference.time.shape == other.time.shape and np.array_equal(reference.time, other.time):
        return other.values
    return np.interp(reference.time, other.time, other.values)


def _require_same_unit(a: Trace, b: Trace, what: str) -> None:
    if a.value_unit != b.value_unit:
        raise ValueError(
            f"cannot {what} traces with different units: {a.value_unit!r} vs {b.value_unit!r}"
        )


def subtract_traces(a: Trace, b: Trace, *, label: str | None = None) -> Trace:
    """``a - b``, ``b`` resampled onto ``a``'s time grid if they differ.

    Both traces must share ``value_unit`` — subtracting volts from amps is
    never meaningful, so this raises ``ValueError`` rather than doing it
    anyway. ``a``'s ``time``/``time_unit`` become the result's; ``b`` only
    contributes its (possibly resampled) values.
    """
    _require_same_unit(a, b, "subtract")
    b_values = _resample_onto(a, b)
    source = f"{a.label} - {b.label}"
    return Trace(
        label=label or source,
        time=a.time,
        values=a.values - b_values,
        time_unit=a.time_unit,
        value_unit=a.value_unit,
        meta={"source": source},
    )


def subtract_background(
    trace: Trace,
    *,
    region: tuple[float, float] | None = None,
    reference: Trace | None = None,
    label: str | None = None,
) -> Trace:
    """Baseline-correct ``trace``.

    Exactly one of two modes, chosen by which keyword is given:

    * ``region=(t0, t1)`` — subtract the mean of ``trace``'s own values
      over that time window (a flat section before a pulse, say). Raises
      ``ValueError`` if no sample falls inside it; ``t0``/``t1`` may be
      given in either order.
    * ``reference=`` another ``Trace`` (a separately captured "dark"/no-signal
      waveform) — subtract it directly, resampled onto ``trace``'s time grid
      like :func:`subtract_traces`, and subject to the same unit check.

    Passing both, or neither, raises ``ValueError``: there's no sensible
    default to fall back to.
    """
    if (region is None) == (reference is None):
        raise ValueError("pass exactly one of region= or reference=")
    if reference is not None:
        _require_same_unit(trace, reference, "background-correct")
        baseline: float | np.ndarray = _resample_onto(trace, reference)
        source = f"{trace.label} - background({reference.label})"
    else:
        assert region is not None
        lo, hi = sorted(region)
        mask = (trace.time >= lo) & (trace.time <= hi)
        if not np.any(mask):
            raise ValueError(f"no samples fall inside the background region {region!r}")
        baseline = float(np.mean(trace.values[mask]))
        source = f"{trace.label} - background(mean {lo:g}..{hi:g} {trace.time_unit})"
    return Trace(
        label=label or f"{trace.label} (bg-corrected)",
        time=trace.time,
        values=trace.values - baseline,
        time_unit=trace.time_unit,
        value_unit=trace.value_unit,
        meta={"source": source},
    )


def scale_trace(
    trace: Trace,
    *,
    x_scale: float = 1.0,
    x_offset: float = 0.0,
    y_scale: float = 1.0,
    y_offset: float = 0.0,
    label: str | None = None,
) -> Trace:
    """Affine-transform both axes: ``time' = time*x_scale + x_offset``,
    ``values' = values*y_scale + y_offset``.

    All four default to the identity, so passing only the ones that matter
    (``y_scale=1e3`` to display millivolts, say) is the common case.
    ``x_scale``/``y_scale`` of zero would collapse an axis to a single
    value — almost certainly a typo for the default ``1.0`` rather than
    something intended — so, like a non-finite input, it raises
    ``ValueError`` instead of silently producing a flat line.
    """
    values = {"x_scale": x_scale, "x_offset": x_offset, "y_scale": y_scale, "y_offset": y_offset}
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, got {value!r}")
    if x_scale == 0 or y_scale == 0:
        raise ValueError("x_scale and y_scale must be nonzero")
    parts = []
    if x_scale != 1.0 or x_offset != 0.0:
        parts.append(f"x*{x_scale:g}+{x_offset:g}")
    if y_scale != 1.0 or y_offset != 0.0:
        parts.append(f"y*{y_scale:g}+{y_offset:g}")
    source = f"{trace.label} ({', '.join(parts)})" if parts else trace.label
    return Trace(
        label=label or source,
        time=trace.time * x_scale + x_offset,
        values=trace.values * y_scale + y_offset,
        time_unit=trace.time_unit,
        value_unit=trace.value_unit,
        meta={"source": source},
    )


def _legend_label(trace: Trace) -> str:
    """One legend entry carrying a trace's provenance, e.g.
    ``"C1 (0.5 V/div, D1M)"`` or ``"C1 - C2 (bg-corrected)"``."""
    meta = trace.meta
    detail = []
    if "volts_per_div" in meta:
        detail.append(f"{meta['volts_per_div']:g} V/div")
    if "coupling" in meta:
        detail.append(str(meta["coupling"]))
    source = meta.get("source")
    if source and source != trace.label:
        detail.append(str(source))
    return f"{trace.label} ({', '.join(detail)})" if detail else trace.label


def build_waveform_figure(traces: Sequence[Trace], *, title: str | None = None) -> Figure:
    """Build (but do not display or save) a matplotlib figure for ``traces``.

    A real ``Figure``/``Axes`` pair — labeled axes, a legend built from each
    trace's provenance, a light grid — mirroring ``experiment.io.
    build_figure`` for sweep results. This is the one place that decides how
    a set of waveforms becomes a figure; :func:`save_waveform_figure` saves
    it as an image, and the Results screen's inline preview extracts its line
    data with ``tui.plotting.figure_series`` — the same path the console
    already uses for any matplotlib figure a script builds — so the
    interactive preview and the static export are never two different
    renderings of the same data.

    Axis labels come from the first trace when every trace shares the same
    unit; mixed units (comparing a raw channel against one rescaled into
    different units, say) get a "(mixed units)" label rather than a guess,
    since there's no single correct number for shared tick marks in that
    case — the per-line legend still carries each trace's own unit via
    ``meta``.
    """
    fig, ax = plt.subplots()
    for trace in traces:
        ax.plot(trace.time, trace.values, label=_legend_label(trace))
    if traces:
        time_units = {t.time_unit for t in traces}
        value_units = {t.value_unit for t in traces}
        time_label = traces[0].time_unit if len(time_units) == 1 else "mixed units"
        value_label = traces[0].value_unit if len(value_units) == 1 else "mixed units"
        ax.set_xlabel(f"Time ({time_label})")
        ax.set_ylabel(f"Signal ({value_label})")
        ax.legend(loc="best", fontsize="small")
        ax.grid(True, alpha=0.3)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    return fig


def save_waveform_figure(traces: Sequence[Trace], path: Path, *, title: str | None = None) -> Path:
    """Build a waveform figure, save it as an image, and release it.

    Uses ``fig.savefig`` rather than ``plt.show()`` — compare
    ``experiment.io.multiplot``, the interactive equivalent for sweep
    results — because this is meant to be called from the Results screen (or
    any non-interactive caller): a blocking GUI window has no display to
    attach to in that context and would just hang. The figure is closed
    immediately afterwards, since pyplot keeps every figure it creates alive
    until explicitly closed and a long session exporting one waveform plot
    after another would otherwise leak them.
    """
    fig = build_waveform_figure(traces, title=title)
    try:
        fig.savefig(path)
    finally:
        plt.close(fig)
    return path
