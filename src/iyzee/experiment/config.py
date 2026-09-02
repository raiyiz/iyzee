"""Shared MXA setup and acquisition helpers for experiment procedures."""

from __future__ import annotations

from dataclasses import dataclass

from iyzee import IP

from ..mxa import KeysightMXA

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


def prepare_analyzer(traces, config: AnalyzerConfig | None = None) -> KeysightMXA:
    """Create and configure an MXA for a measurement procedure."""
    config = config or AnalyzerConfig()
    mx = KeysightMXA(ip=IP.NOISE_ANALYZER)

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
