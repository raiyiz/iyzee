"""MXA setup and the concrete experiment procedures built from it.

These used to be the hand-written ``record_bw_seq()``/``record_freq_seq()``
functions in ``main.py``. Each is now a small ``Step`` describing one
measurement point, plus a factory function that builds the scan and a
``run_*`` procedure that operates on devices owned by the caller.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from ..mxa import KeysightMXA
from ..power import ShutterControl
from ..wavemeter_readout import read_frequency, set_pid_setpoint
from .core import ExperimentContext, Step, StepResult, run_sequence

TRACE_SQZ = 1
TRACE_SHOT = 2


@dataclass(slots=True)
class AnalyzerConfig:
    """Typed configuration for an MXA measurement setup."""

    center_hz: float = 1e6
    span_hz: float = 0
    avg_count: int = 100
    sweep_duration_ms: int = 10
    res_bw_hz: float = 10e4
    avg_type: str = "LOG"
    trig_source: str = "IMM"


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


@dataclass
class BandwidthStep:
    """Acquire squeezing/shot-noise traces at one resolution bandwidth.

    Keeps the experimental VBW relationship explicit: VBW = 2 * RBW.
    """

    rbw_hz: float

    @property
    def label(self) -> str:
        return f"rbw={self.rbw_hz:.0f}Hz"

    def run(self, ctx: ExperimentContext) -> StepResult:
        ctx.mx.set_rbw(self.rbw_hz)
        ctx.mx.set_vbw(self.rbw_hz * 2, auto=False)
        return StepResult(
            label=self.label,
            x_value=self.rbw_hz,
            x_unit="Hz",
            traces={
                "squeezing": acquire_trace(ctx.mx, TRACE_SQZ),
                "shot_noise": acquire_trace(ctx.mx, TRACE_SHOT),
            },
            meta={"rbw_hz": self.rbw_hz, "vbw_hz": self.rbw_hz * 2},
        )


@dataclass
class FrequencyStep:
    """Set the laser frequency setpoint, wait to settle, and acquire.

    The shutter opens only around the squeezing acquisition and is closed
    again before the shot-noise reference trace is captured; that ordering
    is physically meaningful (the shot-noise reference must not include the
    squeezed-light path), not incidental sequencing.
    """

    frequency_thz: float
    wavemeter_channel: int
    relax_time_s: float

    @property
    def label(self) -> str:
        return f"freq={self.frequency_thz:.6f}THz"

    def run(self, ctx: ExperimentContext) -> StepResult:
        if ctx.shutter is None:
            raise ValueError("FrequencyStep requires ctx.shutter to be set")

        set_pid_setpoint(self.frequency_thz, self.wavemeter_channel)
        time.sleep(self.relax_time_s)
        measured_frequency = read_frequency(self.wavemeter_channel)

        try:
            ctx.shutter.open()
            squeezing = acquire_trace(ctx.mx, TRACE_SQZ)
        finally:
            ctx.shutter.close()

        shot_noise = acquire_trace(ctx.mx, TRACE_SHOT)

        return StepResult(
            label=self.label,
            x_value=self.frequency_thz,
            x_unit="THz",
            traces={"squeezing": squeezing, "shot_noise": shot_noise},
            meta={
                "wavemeter_channel": self.wavemeter_channel,
                "relax_time_s": self.relax_time_s,
                "measured_frequency_thz": measured_frequency,
            },
        )


def bandwidth_sweep_steps(rbw_values_hz=None) -> list[BandwidthStep]:
    """Build the standard 20 kHz-stepped RBW sweep, or a custom one."""
    if rbw_values_hz is None:
        rbw_values_hz = [2 * i * 1e4 for i in range(1, 20)]
    return [BandwidthStep(rbw_hz=rbw) for rbw in rbw_values_hz]


def frequency_sweep_steps(
    laser_center_thz: float = 377.1052067,
    wavemeter_channel: int = 4,
    relax_time_s: float = 1.5,
    offsets_thz=None,
) -> list[FrequencyStep]:
    """Build a laser-frequency scan around laser_center_thz."""
    if offsets_thz is None:
        offsets_thz = [i * 10e-6 for i in range(-1, 1)]
    return [
        FrequencyStep(
            frequency_thz=laser_center_thz + offset,
            wavemeter_channel=wavemeter_channel,
            relax_time_s=relax_time_s,
        )
        for offset in offsets_thz
    ]


def bandwidth_sweep_config(
    *, sweep_duration_ms: int = 5, res_bw_hz: float = 24e3
) -> AnalyzerConfig:
    """Return the standard analyzer configuration for an RBW sweep."""
    return AnalyzerConfig(
        center_hz=1e6,
        span_hz=0,
        avg_count=200,
        sweep_duration_ms=sweep_duration_ms,
        res_bw_hz=res_bw_hz,
        trig_source="IMM",
    )


def frequency_sweep_config(*, sweep_duration_ms: int = 10) -> AnalyzerConfig:
    """Return the standard analyzer configuration for a frequency sweep."""
    return AnalyzerConfig(
        center_hz=1.5e6,
        span_hz=0,
        avg_count=150,
        sweep_duration_ms=sweep_duration_ms,
        res_bw_hz=24e3,
    )


def build_bandwidth_sweep(
    rbw_values_hz=None, *, sweep_duration_ms: int = 5, res_bw_hz: float = 24e3
) -> tuple[list[BandwidthStep], AnalyzerConfig]:
    """Build an RBW sweep and the analyzer configuration it needs."""
    return bandwidth_sweep_steps(rbw_values_hz), bandwidth_sweep_config(
        sweep_duration_ms=sweep_duration_ms, res_bw_hz=res_bw_hz
    )


def build_frequency_sweep(
    laser_center_thz: float = 377.1052067,
    wavemeter_channel: int = 4,
    offsets_thz=None,
    *,
    sweep_duration_ms: int = 10,
) -> tuple[list[FrequencyStep], AnalyzerConfig]:
    """Build a frequency sweep and derive its settle time from the analyzer config."""
    config = frequency_sweep_config(sweep_duration_ms=sweep_duration_ms)
    relax_time_s = config.sweep_duration_ms * config.avg_count / 1000
    steps = frequency_sweep_steps(
        laser_center_thz=laser_center_thz,
        wavemeter_channel=wavemeter_channel,
        relax_time_s=relax_time_s,
        offsets_thz=offsets_thz,
    )
    return steps, config


def _run_steps(
    mx,
    steps: Sequence[Step],
    config: AnalyzerConfig,
    *,
    shutter: ShutterControl | None = None,
    on_error: str = "raise",
) -> list[StepResult]:
    """Configure the MXA, build one run context, and execute its steps."""
    prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), config)
    ctx = ExperimentContext(
        mx=mx,
        run_id=uuid.uuid4().hex[:8],
        shutter=shutter,
        config=asdict(config),
    )
    return run_sequence(steps, ctx, on_error=on_error)


def run_bandwidth_sweep(mx, rbw_values_hz=None, *, on_error: str = "raise") -> list[StepResult]:
    """Measure squeezing/shot-noise traces using the caller-owned MXA."""
    steps, config = build_bandwidth_sweep(rbw_values_hz)
    return _run_steps(mx, steps, config, on_error=on_error)


def run_frequency_sweep(
    mx,
    shutter: ShutterControl,
    laser_center_thz: float = 377.1052067,
    wavemeter_channel: int = 4,
    *,
    on_error: str = "raise",
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using caller-owned devices."""
    steps, config = build_frequency_sweep(
        laser_center_thz=laser_center_thz,
        wavemeter_channel=wavemeter_channel,
    )
    return _run_steps(mx, steps, config, shutter=shutter, on_error=on_error)
