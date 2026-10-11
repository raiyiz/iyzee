"""MXA setup and the concrete experiment procedures built from it.

These used to be the hand-written ``record_bw_seq()``/``record_freq_seq()``
functions of the old command-line script. Both sweeps are now built the same way,
from the same three layers:

1. **Step** -- a :class:`SweepStep` subclass is one measurement point. The base class
   owns the sequence (check instruments, move the swept parameter, acquire the
   squeezing/shot-noise pair, package the result); a subclass says only what is
   specific to its axis: :meth:`SweepStep.apply`, plus its label, x value and unit.
2. **Plan** -- ``<kind>_sweep_steps`` and ``<kind>_sweep_config`` build the points and
   the analyzer setup; ``build_<kind>_sweep`` pairs them into a :class:`Sweep`.
3. **Run** -- :func:`run_sweep` configures the analyzer and runs any :class:`Sweep`;
   ``run_<kind>_sweep`` is ``run_sweep(mx, build_<kind>_sweep(...))``.

A new sweep axis is one new ``SweepStep`` subclass plus the three small functions
above, registered in :data:`SWEEP_KINDS`.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ..devices.mxa import KeysightMXA
from ..devices.power import ShutterControl
from ..devices.wavemeter import Wavemeter
from .core import ExperimentContext, StepResult, run_sequence

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


def acquire_pair(mx: Any, shutter: Any | None = None) -> dict[str, Any]:
    """Acquire the squeezing and shot-noise traces, in that order.

    With a ``shutter`` it is open only around the squeezing acquisition and is
    closed again (in a ``finally``) before the shot-noise reference is captured;
    that ordering is physically meaningful (the shot-noise reference must not
    include the squeezed-light path), not incidental sequencing. Without one
    both traces are taken as the setup stands.
    """
    if shutter is None:
        squeezing = acquire_trace(mx, TRACE_SQZ)
    else:
        try:
            shutter.open()
            squeezing = acquire_trace(mx, TRACE_SQZ)
        finally:
            shutter.close()
    shot_noise = acquire_trace(mx, TRACE_SHOT)
    return {"squeezing": squeezing, "shot_noise": shot_noise}


#: Seconds the laser gets to settle on a new PID setpoint before it is read.
SETTLE_TIME_S = 0.5

#: Defaults shared by the three layers (steps, config, builder, runner) of each sweep.
DEFAULT_SWEEP_RBW_HZ = 24e3
BANDWIDTH_SWEEP_DURATION_MS = 5
FREQUENCY_SWEEP_DURATION_MS = 10
DEFAULT_LASER_CENTER_THZ = 377.1052067
DEFAULT_WAVEMETER_CHANNEL = 4


# -- layer 1: the step ------------------------------------------------------------


class SweepStep(ABC):
    """One point of a sweep: move one parameter, then acquire the trace pair.

    :meth:`run` is the same for every sweep, so the sweeps cannot drift apart in
    sequence or in what a ``StepResult`` contains:

    1. :meth:`check` -- the context carries what this step needs;
    2. :meth:`apply` -- set the swept parameter, return the ``meta`` describing it;
    3. :func:`acquire_pair` -- squeezing and shot noise (through the shutter when
       the step uses one);
    4. package a ``StepResult`` from :attr:`label`, :attr:`x_value`, :attr:`x_unit`.

    Subclasses are dataclasses holding the swept value and declare, as class
    attributes, ``kind`` (the sweep's name, also the saved file's name), ``x_unit``
    and ``requires`` (the instrument keys, as in ``lab.INSTRUMENTS``, that must be
    connected; a UI can say what is missing before anything runs).
    """

    kind: ClassVar[str]
    x_unit: ClassVar[str]
    requires: ClassVar[tuple[str, ...]] = ("mxa",)

    @property
    @abstractmethod
    def label(self) -> str:
        """Display name of this point."""

    @property
    @abstractmethod
    def x_value(self) -> float:
        """This point's position on the swept axis, in :attr:`x_unit`."""

    @abstractmethod
    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        """Move the swept parameter to this point; return the ``meta`` that describes it."""

    @property
    def uses_shutter(self) -> bool:
        return "shutter" in self.requires

    def check(self, ctx: ExperimentContext) -> None:
        """Raise before touching any hardware if ``ctx`` lacks what this step needs."""
        if self.uses_shutter and ctx.shutter is None:
            raise ValueError(f"{type(self).__name__} requires ctx.shutter to be set")

    def run(self, ctx: ExperimentContext) -> StepResult:
        self.check(ctx)
        meta = self.apply(ctx)
        traces = acquire_pair(ctx.mx, ctx.shutter if self.uses_shutter else None)
        return StepResult(
            label=self.label,
            x_value=self.x_value,
            x_unit=self.x_unit,
            traces=traces,
            meta=meta,
        )


@dataclass
class BandwidthStep(SweepStep):
    """Acquire squeezing/shot-noise traces at one resolution bandwidth.

    Keeps the experimental VBW relationship explicit: VBW = 2 * RBW.
    """

    rbw_hz: float

    kind: ClassVar[str] = "bandwidth"
    x_unit: ClassVar[str] = "Hz"
    requires: ClassVar[tuple[str, ...]] = ("mxa",)

    @property
    def vbw_hz(self) -> float:
        return self.rbw_hz * 2

    @property
    def label(self) -> str:
        return f"rbw={self.rbw_hz:.0f}Hz"

    @property
    def x_value(self) -> float:
        return self.rbw_hz

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        ctx.mx.set_rbw(self.rbw_hz)
        ctx.mx.set_vbw(self.vbw_hz, auto=False)
        return {"rbw_hz": self.rbw_hz, "vbw_hz": self.vbw_hz}


@dataclass
class FrequencyStep(SweepStep):
    """Set the laser frequency setpoint, wait to settle, read the wavemeter, and acquire.

    The reading is taken after the settle time and right before the traces, and
    is stored with the step as ``meta["measured_frequency_thz"]``.

    The shutter opens only around the squeezing acquisition (see
    :func:`acquire_pair`), so this step needs one in the context.
    """

    frequency_thz: float
    wavemeter_channel: int
    relax_time_s: float = SETTLE_TIME_S

    kind: ClassVar[str] = "frequency"
    x_unit: ClassVar[str] = "THz"
    requires: ClassVar[tuple[str, ...]] = ("mxa", "shutter", "wavemeter")

    @property
    def label(self) -> str:
        return f"freq={self.frequency_thz:.6f}THz"

    @property
    def x_value(self) -> float:
        return self.frequency_thz

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        wavemeter = Wavemeter()
        wavemeter.set_pid_setpoint(self.frequency_thz, self.wavemeter_channel)
        time.sleep(self.relax_time_s)
        measured_frequency = wavemeter.read_frequency(self.wavemeter_channel)
        return {
            "wavemeter_channel": self.wavemeter_channel,
            "relax_time_s": self.relax_time_s,
            "measured_frequency_thz": measured_frequency,
        }


#: Every sweep kind and the step type that makes its points.
SWEEP_KINDS: dict[str, type[SweepStep]] = {
    BandwidthStep.kind: BandwidthStep,
    FrequencyStep.kind: FrequencyStep,
}


# -- layer 2: the plan (points + analyzer setup) ------------------------------------


@dataclass(frozen=True)
class Sweep:
    """A sweep ready to run: its points and the analyzer setup they need."""

    kind: str
    steps: Sequence[SweepStep]
    config: AnalyzerConfig

    @property
    def requires(self) -> tuple[str, ...]:
        """Instrument keys that must be connected to run this sweep."""
        return SWEEP_KINDS[self.kind].requires


def bandwidth_sweep_steps(rbw_values_hz=None) -> list[BandwidthStep]:
    """Build the standard 20 kHz-stepped RBW sweep, or a custom one."""
    if rbw_values_hz is None:
        rbw_values_hz = [2 * i * 1e4 for i in range(1, 20)]
    return [BandwidthStep(rbw_hz=rbw) for rbw in rbw_values_hz]


def frequency_sweep_steps(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_WAVEMETER_CHANNEL,
    relax_time_s: float = SETTLE_TIME_S,
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
    *,
    sweep_duration_ms: int = BANDWIDTH_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
) -> AnalyzerConfig:
    """Return the standard analyzer configuration for an RBW sweep."""
    return AnalyzerConfig(
        center_hz=1e6,
        span_hz=0,
        avg_count=200,
        sweep_duration_ms=sweep_duration_ms,
        res_bw_hz=res_bw_hz,
    )


def frequency_sweep_config(
    *,
    sweep_duration_ms: int = FREQUENCY_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
) -> AnalyzerConfig:
    """Return the standard analyzer configuration for a frequency sweep."""
    return AnalyzerConfig(
        center_hz=1.5e6,
        span_hz=0,
        avg_count=150,
        sweep_duration_ms=sweep_duration_ms,
        res_bw_hz=res_bw_hz,
    )


def build_bandwidth_sweep(
    rbw_values_hz=None,
    *,
    sweep_duration_ms: int = BANDWIDTH_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
) -> Sweep:
    """Build an RBW sweep and the analyzer configuration it needs."""
    return Sweep(
        kind=BandwidthStep.kind,
        steps=bandwidth_sweep_steps(rbw_values_hz),
        config=bandwidth_sweep_config(sweep_duration_ms=sweep_duration_ms, res_bw_hz=res_bw_hz),
    )


def build_frequency_sweep(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_WAVEMETER_CHANNEL,
    offsets_thz=None,
    *,
    sweep_duration_ms: int = FREQUENCY_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
    relax_time_s: float = SETTLE_TIME_S,
) -> Sweep:
    """Build a frequency sweep and the analyzer configuration it needs.

    The settle time is the laser's, not the analyzer's: every acquisition
    restarts the averaging (``INIT:IMM``), so no analyzer-side wait is needed.
    """
    return Sweep(
        kind=FrequencyStep.kind,
        steps=frequency_sweep_steps(
            laser_center_thz=laser_center_thz,
            wavemeter_channel=wavemeter_channel,
            relax_time_s=relax_time_s,
            offsets_thz=offsets_thz,
        ),
        config=frequency_sweep_config(sweep_duration_ms=sweep_duration_ms, res_bw_hz=res_bw_hz),
    )


# -- layer 3: running it ------------------------------------------------------------


def run_sweep(
    mx,
    sweep: Sweep,
    *,
    shutter: ShutterControl | None = None,
    on_error: str = "raise",
) -> list[StepResult]:
    """Configure the caller-owned MXA, build one run context, and run ``sweep``."""
    prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), sweep.config)
    ctx = ExperimentContext(
        mx=mx,
        run_id=uuid.uuid4().hex[:8],
        shutter=shutter,
        config=asdict(sweep.config),
    )
    return run_sequence(sweep.steps, ctx, on_error=on_error)


def run_bandwidth_sweep(
    mx,
    rbw_values_hz=None,
    *,
    sweep_duration_ms: int = BANDWIDTH_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
    on_error: str = "raise",
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using the caller-owned MXA."""
    sweep = build_bandwidth_sweep(
        rbw_values_hz, sweep_duration_ms=sweep_duration_ms, res_bw_hz=res_bw_hz
    )
    return run_sweep(mx, sweep, on_error=on_error)


def run_frequency_sweep(
    mx,
    shutter: ShutterControl,
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_WAVEMETER_CHANNEL,
    offsets_thz=None,
    *,
    sweep_duration_ms: int = FREQUENCY_SWEEP_DURATION_MS,
    res_bw_hz: float = DEFAULT_SWEEP_RBW_HZ,
    relax_time_s: float = SETTLE_TIME_S,
    on_error: str = "raise",
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using caller-owned devices."""
    sweep = build_frequency_sweep(
        laser_center_thz,
        wavemeter_channel,
        offsets_thz,
        sweep_duration_ms=sweep_duration_ms,
        res_bw_hz=res_bw_hz,
        relax_time_s=relax_time_s,
    )
    return run_sweep(mx, sweep, shutter=shutter, on_error=on_error)
