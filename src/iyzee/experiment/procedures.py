"""MXA setup and the experiment procedures built from it.

Both sweeps have one structure. A :class:`SweepStep` subclass is one point: the base class
owns the sequence (check the context, move the swept parameter, acquire the
squeezing/shot-noise pair, package the result) and a subclass supplies only what is specific
to its axis. ``<kind>_sweep_steps`` and ``<kind>_sweep_config`` make the points and the
analyzer setup, ``build_<kind>_sweep`` pairs them into a :class:`Sweep`, and :func:`run_sweep`
runs any of them. A new axis is one subclass, those three functions and an entry in
:data:`SWEEP_KINDS`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Protocol, TypedDict, Unpack

from ..devices.wavemeter import DEFAULT_CHANNEL, Wavemeter
from .core import ExperimentContext, run_sequence, StepResult


TRACE_SQZ = 1
TRACE_SHOT = 2

#: Analyzer sweep time per point; every preset starts here and every caller can override it.
DEFAULT_SWEEP_DURATION_MS = 10
#: Default spacing of a frequency scan: 10 MHz, in THz (the unit the laser is set in).
FREQUENCY_STEP_THZ = 10e-6


class TraceAcquisition(Protocol):
    """The MXA operations required to acquire one trace."""

    def set_trace_update(self, trace_num: int, state: bool = True) -> None: ...
    def single_sweep_wait(self, timeout_sec: float = 30.0) -> bool: ...
    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]: ...


class Analyzer(TraceAcquisition, Protocol):
    """MXA operations needed to prepare and run a measurement sweep."""

    def set_center_freq(self, freq_hz: float) -> None: ...
    def set_span(self, span_hz: float) -> None: ...
    def set_rbw(self, rbw_hz: float | None = None, auto: bool = False) -> None: ...
    def set_vbw(self, vbw_hz: float | None = None, auto: bool = False) -> None: ...
    def set_attenuation_auto(self, state: bool = True) -> None: ...
    def set_trigger_source(self, source: str) -> None: ...
    def set_sweep_duration(self, time: int) -> None: ...
    def set_average_count(self, count: int) -> None: ...
    def set_average_type(self, avg_type: str) -> None: ...
    def set_trace_display(self, trace_num: int | str, state: bool = True) -> None: ...
    def set_trace_mode(self, trace_num: int | str, mode: str) -> None: ...


class Shutter(Protocol):
    """The shutter operations a frequency step needs."""

    def open(self) -> None: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class AnalyzerConfig:
    """Typed configuration for an MXA measurement setup."""

    center_hz: float = 1e6
    span_hz: float = 0
    avg_count: int = 100
    sweep_duration_ms: int = DEFAULT_SWEEP_DURATION_MS
    res_bw_hz: float = 10e4
    avg_type: str = "LOG"
    trig_source: str = "IMM"


def prepare_analyzer(
    mx: Analyzer, traces: Sequence[int], config: AnalyzerConfig | None = None
) -> Analyzer:
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


def acquire_trace(mx: TraceAcquisition, trace_num: int) -> list[float]:
    """Acquire one trace and return its data."""
    mx.set_trace_update(trace_num, True)
    try:
        mx.single_sweep_wait()
        return mx.get_trace_data(trace_num=trace_num, binary=False)
    finally:
        mx.set_trace_update(trace_num, False)


def acquire_pair(mx: TraceAcquisition, shutter: Shutter | None = None) -> dict[str, Any]:
    """Acquire the squeezing and shot-noise traces, in that order.

    A ``shutter`` is open only around the squeezing acquisition and is closed again (in a
    ``finally``) before the shot-noise reference, which must not include the squeezed-light
    path. Without one both traces are taken as the setup stands.
    """
    try:
        if shutter is not None:
            shutter.open()
        squeezing = acquire_trace(mx, TRACE_SQZ)
    finally:
        if shutter is not None:
            shutter.close()
    return {"squeezing": squeezing, "shot_noise": acquire_trace(mx, TRACE_SHOT)}


#: Seconds the laser gets to settle on a new PID setpoint before it is read.
SETTLE_TIME_S = 0.5
DEFAULT_LASER_CENTER_THZ = 377.1052067


class SweepStep:
    """One point of a sweep: move one parameter, then acquire the trace pair.

    :meth:`run` is the same for every sweep, so sweeps cannot drift apart in sequence or in
    what a ``StepResult`` holds. A subclass is a dataclass whose first field is the swept
    value, and declares ``kind`` (the sweep's name, also the saved file's), ``x_unit``,
    ``x_field`` (that field's name), ``label_format`` and, if it needs more than the MXA,
    ``requires`` (instrument keys as in ``lab.INSTRUMENTS``; a UI can say what is missing
    before anything runs). It implements :meth:`apply`.
    """

    kind: ClassVar[str]
    x_unit: ClassVar[str]
    x_field: ClassVar[str]
    label_format: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ("mxa",)

    @property
    def x_value(self) -> float:
        """This point's position on the swept axis, in :attr:`x_unit`."""
        return float(getattr(self, self.x_field))

    @property
    def label(self) -> str:
        return self.label_format.format(self.x_value)

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        """Move the swept parameter to this point; return the ``meta`` that describes it."""
        raise NotImplementedError

    def run(self, ctx: ExperimentContext) -> StepResult:
        missing = [key for key in self.requires if key != "mxa" and getattr(ctx, key) is None]
        if missing:
            raise ValueError(
                f"{type(self).__name__} requires ctx.{', ctx.'.join(missing)} to be set"
            )
        uses_shutter = "shutter" in self.requires
        meta = self.apply(ctx)
        traces = acquire_pair(ctx.mx, ctx.shutter if uses_shutter else None)
        return StepResult(self.label, self.x_value, self.x_unit, traces, meta)


@dataclass
class BandwidthStep(SweepStep):
    """Squeezing/shot-noise traces at one resolution bandwidth (VBW = 2 * RBW)."""

    rbw_hz: float

    kind: ClassVar[str] = "bandwidth"
    x_unit: ClassVar[str] = "Hz"
    x_field: ClassVar[str] = "rbw_hz"
    label_format: ClassVar[str] = "rbw={:.0f}Hz"

    @property
    def vbw_hz(self) -> float:
        return self.rbw_hz * 2

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        ctx.mx.set_rbw(self.rbw_hz)
        ctx.mx.set_vbw(self.vbw_hz, auto=False)
        return {"rbw_hz": self.rbw_hz, "vbw_hz": self.vbw_hz}


@dataclass
class FrequencyStep(SweepStep):
    """Set the laser setpoint, wait to settle, read the wavemeter, then acquire.

    Uses the context's ``wavemeter`` (the connected, lock-protected one in the app). The
    reading is taken after the settle time, right before the traces, and kept as
    ``meta["measured_frequency_thz"]``. The shutter opens only around the squeezing
    acquisition (see :func:`acquire_pair`).
    """

    frequency_thz: float
    wavemeter_channel: int
    relax_time_s: float = SETTLE_TIME_S

    kind: ClassVar[str] = "frequency"
    x_unit: ClassVar[str] = "THz"
    x_field: ClassVar[str] = "frequency_thz"
    label_format: ClassVar[str] = "freq={:.6f}THz"
    requires: ClassVar[tuple[str, ...]] = ("mxa", "shutter", "wavemeter")

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        wavemeter = ctx.wavemeter
        wavemeter.set_pid_setpoint(self.frequency_thz, self.wavemeter_channel)
        time.sleep(self.relax_time_s)
        return {
            "wavemeter_channel": self.wavemeter_channel,
            "relax_time_s": self.relax_time_s,
            "measured_frequency_thz": wavemeter.read_frequency(self.wavemeter_channel),
        }


#: Every sweep kind and the step type that makes its points.
SWEEP_KINDS: dict[str, type[SweepStep]] = {c.kind: c for c in (BandwidthStep, FrequencyStep)}


@dataclass(frozen=True)
class Sweep[StepT: SweepStep]:
    """A sweep ready to run: its points and the analyzer setup they need."""

    kind: str
    steps: Sequence[StepT]
    config: AnalyzerConfig

    @property
    def requires(self) -> tuple[str, ...]:
        """Instrument keys that must be connected to run this sweep."""
        return SWEEP_KINDS[self.kind].requires


class ConfigOptions(TypedDict, total=False):
    """The analyzer settings every sweep lets the caller override."""

    sweep_duration_ms: int
    res_bw_hz: float


def bandwidth_sweep_steps(rbw_values_hz: Sequence[float] | None = None) -> list[BandwidthStep]:
    """Build the standard 20 kHz-stepped RBW sweep, or a custom one."""
    if rbw_values_hz is None:
        rbw_values_hz = [2 * i * 1e4 for i in range(1, 20)]
    return [BandwidthStep(rbw) for rbw in rbw_values_hz]


def frequency_sweep_steps(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_CHANNEL,
    relax_time_s: float = SETTLE_TIME_S,
    offsets_thz: Sequence[float] | None = None,
) -> list[FrequencyStep]:
    """Build a laser-frequency scan around laser_center_thz."""
    if offsets_thz is None:
        offsets_thz = [i * FREQUENCY_STEP_THZ for i in range(-1, 1)]
    return [
        FrequencyStep(laser_center_thz + offset, wavemeter_channel, relax_time_s)
        for offset in offsets_thz
    ]


def bandwidth_sweep_config(
    *, sweep_duration_ms: int = DEFAULT_SWEEP_DURATION_MS, res_bw_hz: float = 24e3
) -> AnalyzerConfig:
    """Return the standard analyzer configuration for an RBW sweep."""
    return AnalyzerConfig(1e6, 0, 200, sweep_duration_ms, res_bw_hz)


def frequency_sweep_config(
    *, sweep_duration_ms: int = DEFAULT_SWEEP_DURATION_MS, res_bw_hz: float = 24e3
) -> AnalyzerConfig:
    """Return the standard analyzer configuration for a frequency sweep."""
    return AnalyzerConfig(1.5e6, 0, 150, sweep_duration_ms, res_bw_hz)


def build_bandwidth_sweep(
    rbw_values_hz: Sequence[float] | None = None, **config: Unpack[ConfigOptions]
) -> Sweep[BandwidthStep]:
    """Build an RBW sweep and the analyzer configuration it needs."""
    return Sweep(
        "bandwidth", bandwidth_sweep_steps(rbw_values_hz), bandwidth_sweep_config(**config)
    )


def build_frequency_sweep(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_CHANNEL,
    offsets_thz: Sequence[float] | None = None,
    *,
    relax_time_s: float = SETTLE_TIME_S,
    **config: Unpack[ConfigOptions],
) -> Sweep[FrequencyStep]:
    """Build a frequency sweep and the analyzer configuration it needs.

    The settle time is the laser's, not the analyzer's: every acquisition restarts the
    averaging (``INIT:IMM``), so no analyzer-side wait is needed.
    """
    steps = frequency_sweep_steps(laser_center_thz, wavemeter_channel, relax_time_s, offsets_thz)
    return Sweep("frequency", steps, frequency_sweep_config(**config))


def run_sweep[StepT: SweepStep](
    mx: Analyzer,
    sweep: Sweep[StepT],
    *,
    shutter: Shutter | None = None,
    wavemeter: Wavemeter | None = None,
    on_error: str = "raise",
) -> list[StepResult]:
    """Configure the caller-owned MXA, build one run context, and run ``sweep``."""
    prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), sweep.config)
    ctx = ExperimentContext(
        mx=mx,
        run_id=uuid.uuid4().hex[:8],
        shutter=shutter,
        config=asdict(sweep.config),
        wavemeter=wavemeter,
    )
    return run_sequence(sweep.steps, ctx, on_error=on_error)


def run_bandwidth_sweep(
    mx: Analyzer,
    rbw_values_hz: Sequence[float] | None = None,
    *,
    on_error: str = "raise",
    **config: Unpack[ConfigOptions]
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using the caller-owned MXA."""
    return run_sweep(mx, build_bandwidth_sweep(rbw_values_hz, **config), on_error=on_error)


def run_frequency_sweep(
    mx: Analyzer,
    shutter: Shutter,
    *args: Any,
    wavemeter: Wavemeter | None = None,
    on_error: str = "raise",
    **kwargs: Any,
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using caller-owned devices.

    ``wavemeter`` defaults to a client for the configured server; everything else after
    ``shutter`` is :func:`build_frequency_sweep`'s arguments.
    """
    if wavemeter is None:
        wavemeter = Wavemeter()
    sweep = build_frequency_sweep(*args, **kwargs)
    return run_sweep(mx, sweep, shutter=shutter, wavemeter=wavemeter, on_error=on_error)
