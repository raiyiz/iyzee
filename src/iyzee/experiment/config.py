"""Shared MXA setup and acquisition helpers for experiment procedures."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..mxa import KeysightMXA

if TYPE_CHECKING:
    from ..tui.instruments import InstrumentHandle, InstrumentSpec

TRACE_SQZ = 1
TRACE_SHOT = 2


@dataclass(slots=True)
class AnalyzerConfig:
    """Typed configuration for an MXA measurement setup."""

    center_hz: float = 1e6
    span_hz: float = 0
    avg_count: int = 100
    sweep_duration_ms: int = 10
    res_bw_hz: float = 10e3
    avg_type: str = "LOG"
    trig_source: str = "EXT"


def instrument_settings(
    spec: InstrumentSpec,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one instrument's defaults with optional local overrides.

    The returned dictionary is independent of the spec and override mappings,
    so callers can safely modify it before constructing an instrument.
    """
    settings = dict(spec.defaults)
    if overrides is not None:
        settings.update(overrides)
    return settings


def select_instruments(
    specs: Iterable[InstrumentSpec],
    required: Iterable[str],
) -> tuple[InstrumentSpec, ...]:
    """Select the instrument specs required by an experiment.

    The order of ``specs`` is preserved so the result stays stable for the
    Connect screen and other callers that present instruments to a user.
    Unknown keys are rejected rather than silently producing an incomplete
    experiment setup.
    """
    required_keys = set(required)
    selected = tuple(spec for spec in specs if spec.key in required_keys)
    selected_keys = {spec.key for spec in selected}
    missing = required_keys - selected_keys
    if missing:
        names = ", ".join(sorted(missing))
        raise KeyError(f"unknown instrument(s): {names}")
    return selected


def build_instruments(
    specs: Iterable[InstrumentSpec],
    required: Iterable[str],
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, InstrumentHandle]:
    """Build only the handles required by an experiment.

    Handles are constructed but not connected; connection ownership remains
    with the caller, just as it does for the existing Connect screen.
    """
    overrides = overrides or {}
    return {
        spec.key: spec.build(overrides.get(spec.key))
        for spec in select_instruments(specs, required)
    }


def prepare_analyzer(mx: KeysightMXA, traces, config: AnalyzerConfig | None = None) -> KeysightMXA:
    """Configure an already-owned MXA for a measurement procedure."""
    config = config or AnalyzerConfig()

    mx.set_center_freq(config.center_hz)
    mx.set_span(config.span_hz)
    mx.set_rbw(config.res_bw_hz)
    mx.set_vbw(config.res_bw_hz, auto=True)
    mx.set_attenuation_auto(True)
    mx.set_trigger_source(config.trig_source)
    mx.set_sweep_duration(config.sweep_duration_ms)
    mx.set_average_count(config.avg_count)
    mx.set_average_type(config.avg_type)

    for trace in traces:
        mx.set_trace_display(trace, True)
        mx.set_trace_mode(trace, "AVER")

    return mx


def acquire_trace(mx, trace_num):
    """Acquire one trace and return its data."""
    mx.set_trace_update(trace_num, True)
    try:
        mx.single_sweep_wait()
        return mx.get_trace_data(trace_num=trace_num, binary=False)
    finally:
        mx.set_trace_update(trace_num, False)
