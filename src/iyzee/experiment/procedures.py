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
import math
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, TypedDict, Unpack

from ..devices.mxa import KeysightMXA
from ..devices.power import ShutterControl
from ..devices.wavemeter import DEFAULT_CHANNEL, Wavemeter
from .core import ExperimentContext, StepResult, run_sequence

TRACE_SQZ = 1
TRACE_SHOT = 2

#: Analyzer sweep time per point; every preset starts here and every caller can override it.
DEFAULT_SWEEP_DURATION_MS = 500
#: Default spacing of a frequency scan: 10 MHz, in THz (the unit the laser is set in).
FREQUENCY_STEP_THZ = 10e-6

MIN_ACQUISITION_TIMEOUT_S = 10.0
ACQUISITION_TIMEOUT_FACTOR = 2.0
ACQUISITION_TIMEOUT_MARGIN_S = 5.0

log = logging.getLogger("iyzee.experiment")


class FatalSweepError(RuntimeError):
    """An instrument acquisition failed; continuing could save stale or invalid data."""


class AcquisitionTimeoutError(FatalSweepError):
    """The analyzer did not finish the requested averaged trace before its deadline."""


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


def validate_analyzer_config(config: AnalyzerConfig) -> None:
    """Validate settings before partially reconfiguring the analyzer."""
    if not math.isfinite(config.center_hz) or config.center_hz <= 0:
        raise ValueError(f"center frequency must be finite and positive, got {config.center_hz!r}")
    if not math.isfinite(config.span_hz) or config.span_hz < 0:
        raise ValueError(f"span must be finite and non-negative, got {config.span_hz!r}")
    if (
        not isinstance(config.avg_count, int)
        or isinstance(config.avg_count, bool)
        or config.avg_count < 1
    ):
        raise ValueError(f"average count must be a positive integer, got {config.avg_count!r}")
    if (
        not isinstance(config.sweep_duration_ms, int)
        or isinstance(config.sweep_duration_ms, bool)
        or config.sweep_duration_ms < 1
    ):
        raise ValueError(
            f"sweep duration must be a positive integer in ms, got {config.sweep_duration_ms!r}"
        )
    if not math.isfinite(config.res_bw_hz) or config.res_bw_hz <= 0:
        raise ValueError(f"resolution bandwidth must be finite and positive, got {config.res_bw_hz!r}")


def acquisition_timeout_s(config: AnalyzerConfig) -> float:
    """Bound one averaged acquisition by its expected work, plus a safety margin."""
    validate_analyzer_config(config)
    expected_s = config.sweep_duration_ms / 1000 * config.avg_count
    return max(
        MIN_ACQUISITION_TIMEOUT_S,
        expected_s * ACQUISITION_TIMEOUT_FACTOR + ACQUISITION_TIMEOUT_MARGIN_S,
    )


def prepare_analyzer(mx: KeysightMXA, traces, config: AnalyzerConfig | None = None) -> KeysightMXA:
    """Configure an already-owned MXA for a measurement procedure."""
    config = config or AnalyzerConfig()
    validate_analyzer_config(config)

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


def _abort_mxa(mx) -> None:
    """Best-effort stop after a failed acquisition, preserving the original failure."""
    try:
        mx.abort_sweep()
    except Exception:
        log.exception("MXA did not accept ABOR after a failed acquisition")


def acquire_trace(
    mx, trace_num: int, *, timeout_sec: float | None = None
) -> list[float]:
    """Acquire a complete trace and fail instead of returning stale data."""
    trace_enabled = False
    successful = False
    started = time.monotonic()
    try:
        mx.set_trace_update(trace_num, True)
        trace_enabled = True
        completed = (
            mx.single_sweep_wait()
            if timeout_sec is None
            else mx.single_sweep_wait(timeout_sec=timeout_sec)
        )
        if not completed:
            wait = 30.0 if timeout_sec is None else timeout_sec
            raise AcquisitionTimeoutError(
                f"MXA trace {trace_num} did not finish within {wait:.1f} s"
            )

        values = [float(value) for value in mx.get_trace_data(trace_num=trace_num, binary=True)]
        if not values:
            raise FatalSweepError(f"MXA trace {trace_num} returned no samples")
        if not all(math.isfinite(value) for value in values):
            raise FatalSweepError(f"MXA trace {trace_num} contains non-finite samples")
        successful = True
        log.info(
            "MXA trace %s: acquired %d samples in %.2f s",
            trace_num,
            len(values),
            time.monotonic() - started,
        )
        return values
    except FatalSweepError:
        _abort_mxa(mx)
        raise
    except Exception as exc:
        _abort_mxa(mx)
        raise FatalSweepError(f"MXA trace {trace_num} acquisition failed: {exc}") from exc
    finally:
        if trace_enabled:
            try:
                mx.set_trace_update(trace_num, False)
            except Exception as exc:
                if successful:
                    raise FatalSweepError(
                        f"MXA trace {trace_num} was acquired, but trace updates could not be disabled: {exc}"
                    ) from exc
                log.exception("could not disable MXA trace %s updates during error cleanup", trace_num)


def acquire_pair(
    mx: Any,
    shutter: Any | None = None,
    *,
    timeout_sec: float | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Acquire the squeezing and shot-noise traces, in that order.

    A ``shutter`` is open only around the squeezing acquisition and is closed again (in a
    ``finally``) before the shot-noise reference, which must not include the squeezed-light
    path. Without one both traces are taken as the setup stands.
    """
    def stage(message: str) -> None:
        if on_stage is not None:
            try:
                on_stage(message)
            except Exception:
                log.exception("sweep stage callback failed")

    def acquire(trace_num: int) -> list[float]:
        if timeout_sec is None:
            return acquire_trace(mx, trace_num)
        return acquire_trace(mx, trace_num, timeout_sec=timeout_sec)

    stage("Acquiring squeezing trace (Trace 1)")
    try:
        if shutter is not None:
            stage("Opening shutter for squeezing trace")
            try:
                shutter.open()
            except Exception as exc:
                raise FatalSweepError(f"could not open shutter for squeezing trace: {exc}") from exc
        squeezing = acquire(TRACE_SQZ)
    except BaseException:
        if shutter is not None:
            try:
                stage("Closing shutter after failed squeezing trace")
                shutter.close()
            except Exception:
                log.exception("could not close shutter while handling a failed squeezing trace")
        raise
    else:
        if shutter is not None:
            stage("Closing shutter before shot-noise reference")
            try:
                shutter.close()
            except Exception as exc:
                try:
                    shutter.close()
                except Exception:
                    log.exception("shutter close retry also failed")
                raise FatalSweepError(
                    f"could not close shutter before shot-noise acquisition: {exc}"
                ) from exc

    stage("Acquiring shot-noise reference (Trace 2)")
    shot_noise = acquire(TRACE_SHOT)
    if len(squeezing) != len(shot_noise):
        raise FatalSweepError(
            f"MXA trace lengths differ: squeezing has {len(squeezing)} samples, "
            f"shot noise has {len(shot_noise)}"
        )
    return {"squeezing": squeezing, "shot_noise": shot_noise}


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
        ctx.report_stage(f"{self.label}: applying point settings")
        meta = self.apply(ctx)
        traces = acquire_pair(
            ctx.mx,
            ctx.shutter if uses_shutter else None,
            timeout_sec=ctx.config.get("acquisition_timeout_s"),
            on_stage=lambda message: ctx.report_stage(f"{self.label}: {message}"),
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

    kind: ClassVar[str] = "frequency"
    x_unit: ClassVar[str] = "THz"
    x_field: ClassVar[str] = "frequency_thz"
    label_format: ClassVar[str] = "freq={:.6f}THz"
    requires: ClassVar[tuple[str, ...]] = ("mxa", "shutter", "wavemeter")

    def apply(self, ctx: ExperimentContext) -> dict[str, Any]:
        if not math.isfinite(self.frequency_thz) or self.frequency_thz <= 0:
            raise ValueError(f"laser frequency must be finite and positive, got {self.frequency_thz!r}")
        if not math.isfinite(self.relax_time_s) or self.relax_time_s < 0:
            raise ValueError(
                f"laser settling time must be finite and non-negative, got {self.relax_time_s!r}"
            )

        wavemeter = ctx.wavemeter
        ctx.report_stage(f"{self.label}: setting PID setpoint to {self.frequency_thz:.9f} THz")
        try:
            wavemeter.set_pid_setpoint(self.frequency_thz, self.wavemeter_channel)
        except Exception as exc:
            raise FatalSweepError(
                f"could not set wavemeter channel {self.wavemeter_channel} to "
                f"{self.frequency_thz:.9f} THz: {exc}"
            ) from exc
        if self.relax_time_s:
            ctx.report_stage(f"{self.label}: allowing {self.relax_time_s:g} s for laser settling")
            time.sleep(self.relax_time_s)
        ctx.report_stage(f"{self.label}: reading wavemeter channel {self.wavemeter_channel}")
        try:
            measured = wavemeter.read_frequency(self.wavemeter_channel)
        except Exception as exc:
            raise FatalSweepError(
                f"could not read wavemeter channel {self.wavemeter_channel}: {exc}"
            ) from exc
        if not math.isfinite(measured) or measured <= 0:
            raise FatalSweepError(f"wavemeter returned an invalid frequency: {measured!r}")
        error_mhz = (measured - self.frequency_thz) * 1e6
        log.info(
            "%s: measured %.9f THz (setpoint error %+.3f MHz)",
            self.label,
            measured,
            error_mhz,
        )
        return {
            "wavemeter_channel": self.wavemeter_channel,
            "relax_time_s": self.relax_time_s,
            "measured_frequency_thz": measured,
            "setpoint_error_mhz": error_mhz,
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


def estimate_sweep_duration_s(sweep: Sweep) -> float:
    """Estimate instrument acquisition and laser-settling time, excluding overhead."""
    validate_analyzer_config(sweep.config)
    one_trace_s = sweep.config.sweep_duration_ms / 1000 * sweep.config.avg_count
    settle_s = sum(
        step.relax_time_s for step in sweep.steps if isinstance(step, FrequencyStep)
    )
    return len(sweep.steps) * 2 * one_trace_s + settle_s


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
    **config: Unpack[ConfigOptions],
) -> Sweep:
    """Build a frequency sweep and the analyzer configuration it needs.

    The settle time is the laser's, not the analyzer's: every acquisition restarts the
    averaging (``INIT:IMM``), so no analyzer-side wait is needed.
    """
    steps = frequency_sweep_steps(laser_center_thz, wavemeter_channel, relax_time_s, offsets_thz)
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
    run_config = asdict(sweep.config)
    run_config["acquisition_timeout_s"] = acquisition_timeout_s(sweep.config)
    ctx = ExperimentContext(
        mx=mx,
        run_id=uuid.uuid4().hex[:8],
        shutter=shutter,
        config=run_config,
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
