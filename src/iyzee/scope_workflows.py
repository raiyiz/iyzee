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
import threading
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

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


def acquire_waveforms(
    scope: LeCroy, channels: Sequence[Channel], *, lock: threading.Lock | None = None
) -> tuple[list[tuple[list[float], list[float], str]], list[ChannelError]]:
    """Download one waveform per channel in ``channels``.

    Returns ``(series, errors)``: ``series`` is ``(x, y, label)`` per
    channel — the shape ``plotting.draw_series`` (or a bare
    matplotlib/plotext call) expects. A channel that failed to acquire is
    absent from ``series`` and present in ``errors`` instead, so a caller
    sees exactly what did and didn't come back rather than the whole
    acquisition failing over one bad channel.

    Every channel shares one timebase (they trigger together, off the
    same clock), so it's read once — off the first channel — and reused,
    rather than once per channel: ``getHorProperties()`` is 3 ``INSPECT?``
    round-trips, and asking it per channel would repeat the same 3
    questions N times over for the same answer. If the scope can't answer
    it, none of the channels could be timestamped anyway, so every channel
    is reported failed together rather than discovering that one at a
    time.
    """
    series: list[tuple[list[float], list[float], str]] = []
    errors: list[ChannelError] = []
    with _locked(lock):
        try:
            _hor_unit, hor_offset, hor_interval = scope.getHorProperties(channel=channels[0])
        except Exception as exc:  # noqa: BLE001
            log.exception("scope: failed to read the timebase from %s", channels[0])
            return [], [ChannelError(channel, exc) for channel in channels]
        for channel in channels:
            try:
                _unit, values = scope.getDataFloats(channel=channel)
                times = [hor_offset + i * hor_interval for i in range(len(values))]
                series.append((times, list(values), str(channel)))
            except Exception as exc:  # noqa: BLE001
                log.exception("scope: failed to acquire %s", channel)
                errors.append(ChannelError(channel, exc))
    return series, errors
