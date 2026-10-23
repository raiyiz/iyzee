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

import math
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar, TypedDict, Unpack

from ..devices.mxa import KeysightMXA
from ..devices.power import ShutterControl
from ..devices.wavemeter import DEFAULT_CHANNEL, Wavemeter
from .core import ExperimentContext, RunRecord, StepResult, run_sequence


class SweepResults(list[StepResult]):
    """Step results carrying the run metadata needed for a self-describing save."""

    def __init__(self, results: Sequence[StepResult], run_metadata: dict[str, Any]) -> None:
        super().__init__(results)
        self.run_metadata = run_metadata


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


def read_analyzer_settings(mx: Any) -> dict[str, float | int]:
    """Read back the MXA settings whose quantization affects saved data.

    The duration returned by ``SWE:TIME?`` is stored in seconds; RBW is in
    hertz and averaging count is unitless. A non-MXA test double without a
    ``query`` method returns an empty mapping; a connected Keysight MXA must
    answer all three queries or setup fails rather than recording requested
    settings as if they were applied.
    """
    query = getattr(mx, "query", None)
    if not callable(query):
        return {}

    rbw_hz = float(query("BWID?"))
    sweep_duration_s = float(query("SWE:TIME?"))
    average_count = float(query("AVER:COUN?"))
    if not math.isfinite(rbw_hz) or rbw_hz <= 0:
        raise ValueError(f"MXA returned invalid applied RBW {rbw_hz!r} Hz")
    if not math.isfinite(sweep_duration_s) or sweep_duration_s <= 0:
        raise ValueError(f"MXA returned invalid applied sweep duration {sweep_duration_s!r} s")
    if not math.isfinite(average_count) or not average_count.is_integer() or average_count < 1:
        raise ValueError(f"MXA returned invalid averaging count {average_count!r}")
    return {
        "rbw_hz": rbw_hz,
        "sweep_duration_s": sweep_duration_s,
        "average_count": int(average_count),
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

    errors = mx.get_errors()
    if errors:
        raise RuntimeError("MXA rejected analyzer configuration: " + "; ".join(errors))
    return mx


def acquire_trace(mx, trace_num):
    """Acquire one trace and return its data, refusing an incomplete sweep."""
    mx.set_trace_update(trace_num, True)
    try:
        if not mx.single_sweep_wait():
            raise TimeoutError(f"MXA sweep for trace {trace_num} did not complete")
        data = mx.get_trace_data(trace_num=trace_num, binary=False)
        errors = mx.get_errors()
        if errors:
            raise RuntimeError(
                f"MXA reported errors while acquiring trace {trace_num}: " + "; ".join(errors)
            )
        return data
    finally:
        # A VISA timeout invalidates the MXA connection. Do not let cleanup try
        # another command on that already-dropped resource and mask the timeout.
        if getattr(mx, "instrument", object()) is not None:
            mx.set_trace_update(trace_num, False)


#: Default optical shutter settling delay after a verified state transition.
SHUTTER_SETTLE_TIME_S = 0.05


def acquire_pair(
    mx: Any,
    shutter: Any | None = None,
    *,
    settle_time_s: float = SHUTTER_SETTLE_TIME_S,
) -> dict[str, Any]:
    """Acquire squeezing and shot-noise traces, verifying shutter transitions.

    A short settle delay is applied after a verified open and close transition.
    The delay is configurable for experiments and should be verified against the
    shutter's measured physical response time on the bench.
    """
    if settle_time_s < 0:
        raise ValueError(f"settle_time_s must be non-negative, got {settle_time_s}")
    try:
        if shutter is not None:
            shutter.open()
            if not shutter.is_open():
                raise RuntimeError(
                    "shutter did not open; check the PSU master output and shutter channel"
                )
            if settle_time_s:
                time.sleep(settle_time_s)
        squeezing = acquire_trace(mx, TRACE_SQZ)
    finally:
        if shutter is not None:
            safe_close = getattr(shutter, "safe_close", None)
            if callable(safe_close):
                safe_close()
            else:
                shutter.close()
                if shutter.is_open():
                    raise RuntimeError("shutter remained open after close")
            if settle_time_s:
                time.sleep(settle_time_s)
    return {"squeezing": squeezing, "shot_noise": acquire_trace(mx, TRACE_SHOT)}


#: Minimum initial settle delay after a new PID setpoint.
SETTLE_TIME_S = 0.5
#: Stop the run rather than acquiring at a frequency that never reaches this tolerance.
FREQUENCY_LOCK_TOLERANCE_THZ = 10e-6  # 10 MHz
FREQUENCY_LOCK_TIMEOUT_S = 10.0
FREQUENCY_LOCK_POLL_INTERVAL_S = 0.1
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
        requested_rbw_hz = self.rbw_hz
        requested_vbw_hz = self.vbw_hz
        ctx.mx.set_rbw(requested_rbw_hz)
        ctx.mx.set_vbw(requested_vbw_hz, auto=False)

        query = getattr(ctx.mx, "query", None)
        if callable(query):
            applied_rbw_hz = float(query("BWID?"))
            applied_vbw_hz = float(query("BWID:VID?"))
            if (
                not math.isfinite(applied_rbw_hz)
                or applied_rbw_hz <= 0
                or not math.isfinite(applied_vbw_hz)
                or applied_vbw_hz <= 0
            ):
                raise ValueError(
                    f"MXA returned invalid bandwidths: RBW={applied_rbw_hz!r}, "
                    f"VBW={applied_vbw_hz!r}"
                )
        else:
            applied_rbw_hz, applied_vbw_hz = requested_rbw_hz, requested_vbw_hz

        # The sweep's x coordinate must describe what the analyzer applied, not
        # just the requested point that may have been quantized by the hardware.
        self.rbw_hz = applied_rbw_hz
        return {
            "requested_rbw_hz": requested_rbw_hz,
            "requested_vbw_hz": requested_vbw_hz,
            "rbw_hz": applied_rbw_hz,
            "vbw_hz": applied_vbw_hz,
        }


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

        deadline = time.monotonic() + FREQUENCY_LOCK_TIMEOUT_S
        while True:
            measured = wavemeter.read_frequency(self.wavemeter_channel)
            if abs(measured - self.frequency_thz) <= FREQUENCY_LOCK_TOLERANCE_THZ:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"laser did not reach {self.frequency_thz:.9f} THz within "
                    f"{FREQUENCY_LOCK_TIMEOUT_S:g}s; last reading was {measured:.9f} THz"
                )
            time.sleep(min(FREQUENCY_LOCK_POLL_INTERVAL_S, remaining))

        return {
            "wavemeter_channel": self.wavemeter_channel,
            "relax_time_s": self.relax_time_s,
            "measured_frequency_thz": measured,
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
) -> list[FrequencyStep]:
    """Build a laser-frequency scan around laser_center_thz."""
    if offsets_thz is None:
        # Five points around the requested center: -2, -1, 0, +1, +2 steps.
        offsets_thz = [i * FREQUENCY_STEP_THZ for i in range(-2, 3)]
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
) -> SweepResults:
    """Run a sweep and attach the run record for automatic persistence by ``save_step_results``."""
    record = RunRecord(config=asdict(sweep.config))
    ctx = ExperimentContext(
        mx=mx,
        run_id=record.run_id,
        shutter=shutter,
        config=record.config,
        wavemeter=wavemeter,
    )
    try:
        prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), sweep.config)
        record.config["applied_settings"] = read_analyzer_settings(mx)
        results = run_sequence(sweep.steps, ctx, on_error=on_error, on_step=record.on_step)
    except Exception:
        record.finish(failed=True)
        raise
    record.finish()
    return SweepResults(results, record.as_metadata())


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
