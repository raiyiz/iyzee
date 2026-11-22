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

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, TypedDict, Unpack

import numpy as np

from ..devices.mxa import KeysightMXA, MXAProtocolError, MXATimeoutError
from ..devices.power import ShutterControl, ShutterError
from ..devices.wavemeter import DEFAULT_CHANNEL, Wavemeter
from .core import ExperimentContext, StepResult, run_sequence

log = logging.getLogger("iyzee.experiment")

TRACE_SQZ = 1
TRACE_SHOT = 2

#: Analyzer sweep time per point; every preset starts here and every caller can override it.
DEFAULT_SWEEP_DURATION_MS = 10
#: Default spacing of a frequency scan: 10 MHz, in THz (the unit the laser is set in).
FREQUENCY_STEP_THZ = 10e-6


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


def _checked(trace: Sequence[float], trace_num: int) -> Sequence[float]:
    """The trace itself, if it is usable: not empty and every sample a finite number."""
    values = np.asarray(trace, dtype=np.float64)
    if values.size == 0 or not np.isfinite(values).all():
        raise MXAProtocolError(f"trace {trace_num} came back empty or with non-finite samples")
    return trace


def acquire_trace(mx, trace_num, *, timeout_s: float | None = None):
    """Acquire one trace and return its data.

    A sweep that does not finish in time is an error (:class:`MXATimeoutError`; the analyzer
    has been aborted and its session cleared), never a trace read off an unfinished sweep.
    ``timeout_s`` defaults to what the analyzer's own settings call for.
    """
    mx.set_trace_update(trace_num, True)
    try:
        finished = (
            mx.single_sweep_wait()
            if timeout_s is None
            else mx.single_sweep_wait(timeout_sec=timeout_s)
        )
        if not finished:
            raise MXATimeoutError(f"trace {trace_num}: the sweep did not finish in time")
        data = _checked(mx.get_trace_data(trace_num=trace_num, binary=False), trace_num)
    except BaseException:
        try:  # the failure in flight is the one to report; this is only tidying up
            mx.set_trace_update(trace_num, False)
        except Exception:
            log.exception("could not freeze trace %s after a failed acquisition", trace_num)
        raise
    mx.set_trace_update(trace_num, False)
    return data


#: Seconds to let the shutter finish moving after it was told to open or close.
SHUTTER_SETTLE_S = 0.05
#: A shutter that will not close is the one failure that spoils the shot-noise reference
#: without any sign in the data, so closing is tried this many times before giving up.
SHUTTER_CLOSE_ATTEMPTS = 3


def _close_shutter(shutter: Any) -> None:
    """Close the shutter or raise :class:`ShutterError`; never leave it open quietly."""
    failure: Exception | None = None
    for attempt in range(1, SHUTTER_CLOSE_ATTEMPTS + 1):
        try:
            shutter.close()
            return
        except Exception as exc:
            failure = exc
            log.exception("shutter did not close (attempt %d/%d)", attempt, SHUTTER_CLOSE_ATTEMPTS)
    log.critical("THE SHUTTER MAY STILL BE OPEN: it could not be closed: %s", failure)
    raise ShutterError(f"could not close the shutter: {failure}") from failure


def acquire_pair(
    mx: Any,
    shutter: Any | None = None,
    *,
    ctx: ExperimentContext | None = None,
    settle_s: float = SHUTTER_SETTLE_S,
) -> dict[str, Any]:
    """Acquire the squeezing and shot-noise traces, in that order.

    A ``shutter`` is open only around the squeezing acquisition and is closed again (in a
    ``finally``, retried, and loudly an error if it will not close) before the shot-noise
    reference, which must not include the squeezed-light path. ``settle_s`` is given to it to
    finish moving after each change. Without a shutter both traces are taken as the setup
    stands. With a ``ctx`` the stages are reported (``ctx.phase``) and an abort request is
    honoured between them, with the shutter closed.
    """

    def say(text: str) -> None:
        if ctx is not None:
            ctx.phase(text)

    def pause() -> None:
        if ctx is not None:
            ctx.sleep(settle_s)
        elif settle_s > 0:
            time.sleep(settle_s)

    try:
        if shutter is not None:
            say("opening the shutter")
            shutter.open()
            pause()
        say("acquiring the squeezing trace")
        squeezing = acquire_trace(mx, TRACE_SQZ)
    finally:
        if shutter is not None:
            say("closing the shutter")
            _close_shutter(shutter)
    if shutter is not None:
        pause()
    if ctx is not None:
        ctx.check_abort()
    say("acquiring the shot-noise trace")
    return {"squeezing": squeezing, "shot_noise": acquire_trace(mx, TRACE_SHOT)}


#: Seconds the laser gets to settle on a new PID setpoint before it is read.
SETTLE_TIME_S = 0.5
DEFAULT_LASER_CENTER_THZ = 377.1052067
#: How often the wavemeter is read again while waiting for the laser to reach its setpoint.
SETTLE_POLL_S = 0.1


class LaserNotSettledError(RuntimeError):
    """The wavemeter never read within the tolerance of the setpoint."""


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
    #: Seconds the shutter gets to move (see :func:`acquire_pair`); a subclass may make it a field.
    shutter_settle_s: float = SHUTTER_SETTLE_S

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
        ctx.check_abort()
        meta = self.apply(ctx)
        traces = acquire_pair(
            ctx.mx,
            ctx.shutter if uses_shutter else None,
            ctx=ctx,
            settle_s=self.shutter_settle_s,
        )
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
    shutter_settle_s: float = SHUTTER_SETTLE_S
    #: Opt-in lock check: after ``relax_time_s``, keep reading (every ``SETTLE_POLL_S``, for at
    #: most ``settle_timeout_s``) until the wavemeter is within this many THz of the setpoint;
    #: otherwise the point fails with :class:`LaserNotSettledError`. ``None`` records only.
    settle_tolerance_thz: float | None = None
    settle_timeout_s: float = 10.0

    kind: ClassVar[str] = "frequency"
    x_unit: ClassVar[str] = "THz"
    x_field: ClassVar[str] = "frequency_thz"
    label_format: ClassVar[str] = "freq={:.6f}THz"
    requires: ClassVar[tuple[str, ...]] = ("mxa", "shutter", "wavemeter")

    def _read_settled(self, ctx: ExperimentContext) -> float:
        """The wavemeter reading, after waiting for the setpoint if a tolerance was asked for."""
        wavemeter = ctx.wavemeter
        reading = wavemeter.read_frequency(self.wavemeter_channel)
        tolerance = self.settle_tolerance_thz
        if tolerance is None:
            return reading
        for _ in range(max(1, round(self.settle_timeout_s / SETTLE_POLL_S))):
            if abs(reading - self.frequency_thz) <= tolerance:
                return reading
            ctx.sleep(SETTLE_POLL_S)
            reading = wavemeter.read_frequency(self.wavemeter_channel)
        if abs(reading - self.frequency_thz) <= tolerance:
            return reading
        raise LaserNotSettledError(
            f"the wavemeter reads {reading:.9f} THz, {abs(reading - self.frequency_thz) * 1e6:.2f} MHz "
            f"from the setpoint {self.frequency_thz:.9f} THz after {self.settle_timeout_s:g} s"
        )

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        ctx.phase(f"setting the laser to {self.frequency_thz:.7f} THz")
        ctx.wavemeter.set_pid_setpoint(self.frequency_thz, self.wavemeter_channel)
        ctx.phase(f"waiting {self.relax_time_s:g} s for the laser to settle")
        ctx.sleep(self.relax_time_s)
        ctx.phase("reading the wavemeter")
        measured = self._read_settled(ctx)
        return {
            "wavemeter_channel": self.wavemeter_channel,
            "relax_time_s": self.relax_time_s,
            "shutter_settle_s": self.shutter_settle_s,
            "measured_frequency_thz": measured,
            "setpoint_error_thz": measured - self.frequency_thz,
        }


#: Every sweep kind and the step type that makes its points.
SWEEP_KINDS: dict[str, type[SweepStep]] = {c.kind: c for c in (BandwidthStep, FrequencyStep)}


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


class ConfigOptions(TypedDict, total=False):
    """The analyzer settings every sweep lets the caller override."""

    sweep_duration_ms: int
    res_bw_hz: float


def bandwidth_sweep_steps(rbw_values_hz=None) -> list[BandwidthStep]:
    """Build the standard 20 kHz-stepped RBW sweep, or a custom one."""
    if rbw_values_hz is None:
        rbw_values_hz = [2 * i * 1e4 for i in range(1, 20)]
    return [BandwidthStep(rbw) for rbw in rbw_values_hz]


def frequency_sweep_steps(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_CHANNEL,
    relax_time_s: float = SETTLE_TIME_S,
    offsets_thz=None,
    *,
    settle_tolerance_thz: float | None = None,
) -> list[FrequencyStep]:
    """Build a laser-frequency scan around laser_center_thz."""
    if offsets_thz is None:
        offsets_thz = [i * FREQUENCY_STEP_THZ for i in range(-1, 1)]
    return [
        FrequencyStep(
            laser_center_thz + offset,
            wavemeter_channel,
            relax_time_s,
            settle_tolerance_thz=settle_tolerance_thz,
        )
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


def build_bandwidth_sweep(rbw_values_hz=None, **config: Unpack[ConfigOptions]) -> Sweep:
    """Build an RBW sweep and the analyzer configuration it needs."""
    return Sweep(
        "bandwidth", bandwidth_sweep_steps(rbw_values_hz), bandwidth_sweep_config(**config)
    )


def build_frequency_sweep(
    laser_center_thz: float = DEFAULT_LASER_CENTER_THZ,
    wavemeter_channel: int = DEFAULT_CHANNEL,
    offsets_thz=None,
    *,
    relax_time_s: float = SETTLE_TIME_S,
    settle_tolerance_thz: float | None = None,
    **config: Unpack[ConfigOptions],
) -> Sweep:
    """Build a frequency sweep and the analyzer configuration it needs.

    The settle time is the laser's, not the analyzer's: every acquisition restarts the
    averaging (``INIT:IMM``), so no analyzer-side wait is needed.
    """
    steps = frequency_sweep_steps(
        laser_center_thz,
        wavemeter_channel,
        relax_time_s,
        offsets_thz,
        settle_tolerance_thz=settle_tolerance_thz,
    )
    return Sweep("frequency", steps, frequency_sweep_config(**config))


def run_sweep(
    mx,
    sweep: Sweep,
    *,
    shutter: ShutterControl | None = None,
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
    mx, rbw_values_hz=None, *, on_error: str = "raise", **config: Unpack[ConfigOptions]
) -> list[StepResult]:
    """Measure squeezing/shot-noise traces using the caller-owned MXA."""
    return run_sweep(mx, build_bandwidth_sweep(rbw_values_hz, **config), on_error=on_error)


def run_frequency_sweep(
    mx,
    shutter: ShutterControl,
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
