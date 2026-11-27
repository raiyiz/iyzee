"""Sweep screen: configure and run a bandwidth or frequency sweep.

This deliberately does *not* call the convenience
``run_bandwidth_sweep``/``run_frequency_sweep`` wrappers in
``experiment.procedures`` — those don't take a progress callback. Instead
it composes the same lower-level pieces they're built from
(``AnalyzerConfig``, ``prepare_analyzer``, ``ExperimentContext``,
``run_sequence``) directly, which is exactly what that layer is meant for:
a new caller with different needs (here, live progress) is a few lines
against the existing building blocks, not a change to ``experiment/``.

Guide: :guide:`sec-pages`
"""

from __future__ import annotations

import logging
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING, Any

import numpy as np
from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import (
    Button,
    Input,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

from ...devices.wavemeter import Rb_transitions
from ...experiment.procedures import DEFAULT_LASER_CENTER_THZ
from ...experiment import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    TRACE_SQZ,
    Step,
    StepResult,
    Sweep,
    SweepRunner,
    acquire_trace,
    build_bandwidth_sweep,
    build_frequency_sweep,
    difference_series,
)
from ..commands import Command
from ..plotting import draw_series, prepare_series
from ..text import one_line
from ..workers import LastRun
from .page import FieldError, Page, _field, _positive_float, _positive_int

if TYPE_CHECKING:
    pass

log = logging.getLogger("iyzee.tui")


# Upper bound on "Steps" / "Points". Each step is a full analyzer averaging
# run (seconds), so 1000 is already a multi-minute measurement; the cap
# exists to turn a stray extra zero (or 10**9) into a clear message instead
# of a frozen UI and gigabytes of linspace.
MAX_POINTS = 1000
LIVE_PLOT_INTERVAL_S = 0.1

_RB_FREQUENCIES_THZ = {
    label: frequency for label, frequency in Rb_transitions if not label.endswith("_center")
}
_RB_FREQUENCY_OPTIONS = [("Manual frequency", "manual")] + [
    (f"{label} · {frequency:.9f} THz", label)
    for label, frequency in Rb_transitions
    if not label.endswith("_center")
]
_MXA_AVERAGE_OPTIONS = [
    ("Log power (LOG)", "LOG"),
    ("RMS power (RMS)", "RMS"),
    ("Scalar (SCAL)", "SCAL"),
]
_MXA_TRIGGER_OPTIONS = [
    ("Immediate (IMM)", "IMM"),
    ("Video (VID)", "VID"),
    ("External (EXT)", "EXT"),
    ("RF burst (RFB)", "RFB"),
    ("Frame (FRAM)", "FRAM"),
]

# How each instrument key a sweep ``requires`` is named in "connect the ..." messages.
_INSTRUMENT_NAMES = {"mxa": "MXA", "shutter": "shutter", "wavemeter": "wavemeter"}


def _non_negative_float(raw: str, field: str) -> float:
    """Parse a finite float that may be zero (as an MXA span may be)."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not np.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


class SweepScreen(Page):
    """Pick a sweep type, configure it, run it, and watch it live.

    The form, controls, plot and log together are taller than a small
    terminal, so this is a scrolling ``Page`` rather than a plain
    ``Vertical`` (which silently clips whatever doesn't fit — that is how
    the Run/Abort buttons ended up unreachable). The responsive reflow
    itself lives in ``app.tcss`` (see ``IyzeeApp.HORIZONTAL_BREAKPOINTS``).
    """

    def compose(self) -> ComposeResult:
        yield Static("Sweep", classes="panel-title")
        # Shown only while something the selected sweep needs isn't
        # connected; see refresh_readiness().
        yield Static("", id="sweep-status")
        with Vertical(id="sweep-form"):
            with Grid(id="sweep-common", classes="field-grid"):
                yield _field(
                    "Sweep type",
                    Select(
                        [("Bandwidth sweep", "bandwidth"), ("Frequency sweep", "frequency")],
                        value="bandwidth",
                        allow_blank=False,
                        id="sweep-type",
                    ),
                    id="sweep-type-field",
                )
            with Grid(id="bw-fields", classes="field-grid"):
                yield _field("RBW start (Hz)", Input(value="20000", id="rbw-start"))
                yield _field("RBW stop (Hz)", Input(value="380000", id="rbw-stop"))
                yield _field("Steps", Input(value="19", id="rbw-steps"))
            with Vertical(id="mxa-fields"):
                yield Static("MXA settings", classes="panel-title")
                with Grid(id="mxa-settings", classes="field-grid"):
                    yield _field("Averages", Input(value="200", id="mxa-averages"))
                    yield _field(
                        "Sweep time (ms)",
                        Input(value=str(DEFAULT_SWEEP_DURATION_MS), id="mxa-sweep-time"),
                    )
                    yield _field("Center frequency (MHz)", Input(value="1", id="mxa-center"))
                    yield _field("Span (MHz)", Input(value="0", id="mxa-span"))
                    yield _field(
                        "Averaging type",
                        Select(
                            _MXA_AVERAGE_OPTIONS,
                            value="LOG",
                            allow_blank=False,
                            id="mxa-average-type",
                        ),
                    )
                    yield _field(
                        "Trigger source",
                        Select(
                            _MXA_TRIGGER_OPTIONS,
                            value="IMM",
                            allow_blank=False,
                            id="mxa-trigger-source",
                        ),
                    )
                with Horizontal(id="mxa-actions"):
                    yield Button("Read from MXA", id="read-mxa-settings")
            with Grid(id="freq-fields", classes="field-grid"):
                yield _field(
                    "Laser frequency source",
                    Select(
                        _RB_FREQUENCY_OPTIONS,
                        value="manual",
                        allow_blank=False,
                        id="freq-reference",
                    ),
                )
                yield _field(
                    "Laser center (THz)",
                    Input(value=f"{DEFAULT_LASER_CENTER_THZ:.7f}", id="freq-center"),
                    id="freq-center-field",
                )
                yield _field("Wavemeter channel", Input(value="4", id="freq-channel"))
                yield _field(
                    "Laser settling time (ms)",
                    Input(value="500", id="freq-settle-time"),
                )
                yield _field("Points", Input(value="5", id="freq-points"))
                yield _field(
                    "Offset step (MHz)",
                    Input(value=f"{FREQUENCY_STEP_THZ * 1e6:g}", id="freq-offset-mhz"),
                )
        with Grid(id="sweep-controls"):
            yield Button("Run sweep", id="run-sweep", variant="success")
            yield Button("Abort", id="abort-sweep", variant="error", disabled=True)
            yield Button("Capture trace", id="capture-trace")
        yield Static("", id="sweep-phase")
        yield ProgressBar(id="sweep-progress", total=1)
        yield PlotextPlot(id="sweep-plot")
        yield RichLog(id="sweep-log", highlight=False, markup=True)

    def on_mount(self) -> None:
        self._abort_event = Event()
        self._runner: SweepRunner | None = None  # the current (or last) run
        self._next_live_plot = 0.0
        self._phase = ""
        self._phase_since = monotonic()
        self._phase_timer: Timer | None = None
        self.query_one("#freq-fields").display = False
        self.query_one("#freq-center-field").display = True
        plot = self.query_one("#sweep-plot", PlotextPlot)
        plot.plt.title("Squeezing - shot noise")
        plot.plt.xlabel("Trace point")
        plot.plt.ylabel("Squeezing - shot noise")
        self.refresh_readiness()

    def on_show(self) -> None:
        self.refresh_readiness()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sweep-type":
            is_bandwidth = event.value == "bandwidth"
            self.query_one("#bw-fields").display = is_bandwidth
            self.query_one("#mxa-fields").display = is_bandwidth
            self.query_one("#freq-fields").display = event.value == "frequency"
            self.refresh_readiness()
        elif event.select.id == "freq-reference":
            self.query_one("#freq-center-field").display = event.value == "manual"

    # -- readiness ---------------------------------------------------------

    def _missing_instruments(self, kind: object) -> list[str]:
        """Instrument keys the given sweep needs that aren't connected, in the
        order the sweep lists them (so the first is the one to connect first)."""
        handles = self.iyzee_app.handles
        return [
            key
            for key in SWEEP_KINDS[str(kind)].requires
            if (handle := handles.get(key)) is None or handle.device is None
        ]

    def refresh_readiness(self) -> None:
        """Show, at the top of the page, why Run/Capture won't work yet.

        Previously the only sign was a 5-second toast *after* pressing Run,
        which a first-time user (who opens Sweep first) easily misses. The
        banner appears only while something is missing and is refreshed by
        ``IyzeeApp.instruments_changed`` as connections come and go. The
        buttons themselves stay enabled on purpose: a disabled button gives
        no way to ask "why?", while pressing an enabled one still explains.
        """
        kind = self.query_one("#sweep-type", Select).value
        missing = self._missing_instruments(kind)
        status = self.query_one("#sweep-status", Static)
        if missing:
            status.update(
                "Not ready: connect the "
                + " and the ".join(_INSTRUMENT_NAMES[key] for key in missing)
                + " first — press F1 for the Connect page."
            )
        status.display = bool(missing)

    def commands(self) -> list[Command]:
        return [
            Command(
                "run", lambda _: self.press_button("run-sweep", "start the sweep"), "Run the sweep"
            ),
            Command(
                "abort",
                lambda _: self.press_button("abort-sweep", "abort the sweep"),
                "Abort the running sweep",
            ),
            Command(
                "capture",
                lambda _: self.press_button("capture-trace", "capture a trace"),
                "Capture a single trace",
            ),
        ]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-sweep":
            self._start_sweep()
        elif event.button.id == "abort-sweep":
            self._request_abort()
        elif event.button.id == "capture-trace":
            self._start_capture()
        elif event.button.id == "read-mxa-settings":
            self._start_read_mxa_settings()

    def _request_abort(self) -> None:
        """Abort takes effect at the next stage boundary (between setting the
        laser, the two acquisitions and the next point), not instantly — an
        analyzer averaging run in flight can't be interrupted. So acknowledge
        the click visibly, otherwise a silently greyed-out button looks like
        nothing happened."""
        self._abort_event.set()
        button = self.query_one("#abort-sweep", Button)
        button.disabled = True
        button.label = "Aborting…"
        self.query_one("#sweep-log", RichLog).write(
            "[yellow]Abort requested — finishing the current step, then stopping.[/yellow]"
        )

    # -- kicking off the run -------------------------------------------------

    def _start_sweep(self) -> None:
        kind = str(self.query_one("#sweep-type", Select).value)
        missing = self._missing_instruments(kind)
        if missing:
            self.notify(
                f"Connect the {_INSTRUMENT_NAMES[missing[0]]} first — press F1 for the Connect page.",
                severity="error",
            )
            return

        build = {"bandwidth": self._build_bandwidth_run, "frequency": self._build_frequency_run}
        try:
            sweep = build[kind]()
        except ValueError as exc:  # includes FieldError: a form field that didn't parse
            if isinstance(exc, FieldError):
                self._flag_invalid(exc.field_id)
            self.notify(f"Invalid sweep parameters: {exc}", severity="error", markup=False)
            return

        handles = self.iyzee_app.handles
        devices = {key: handles[key].device for key in sweep.requires}

        self._abort_event = Event()
        self._next_live_plot = monotonic()
        self._runner = SweepRunner(
            sweep,
            devices,
            [handles[key].lock for key in sweep.requires],
            on_point=self._on_point,
            should_abort=self._should_abort,
            on_save_error=self._report_save_error,
            on_phase=self._on_phase,
        )
        log = self.query_one("#sweep-log", RichLog)
        log.clear()
        plot = self.query_one("#sweep-plot", PlotextPlot)
        # #sweep-plot is shared with "Capture trace" (a raw power-vs-
        # frequency spectrum, different axes entirely) — draw_series with
        # an empty series still resets title/xlabel/ylabel, which a bare
        # clear_data() wouldn't, so a sweep after a capture doesn't keep
        # showing the capture's axis labels.
        draw_series(
            plot,
            [],
            title="Squeezing - shot noise",
            xlabel="Trace point",
            ylabel="Squeezing - shot noise",
        )
        progress = self.query_one("#sweep-progress", ProgressBar)
        progress.update(total=len(sweep.steps), progress=0)

        self.query_one("#run-sweep", Button).disabled = True
        self.query_one("#abort-sweep", Button).disabled = False
        self.query_one("#capture-trace", Button).disabled = True
        self.iyzee_app.sweep_running = True
        self.iyzee_app.session_note(f"{kind} sweep running ({len(sweep.steps)} points)")
        self._set_phase("starting")
        self._phase_timer = self.set_interval(0.5, self._show_phase)
        self._run(self._runner)

    def _build_bandwidth_run(self) -> Sweep:
        start = self._read("rbw-start", _positive_float, "RBW start")
        stop = self._read("rbw-stop", _positive_float, "RBW stop")
        count = self._read("rbw-steps", _positive_int, "Steps", maximum=MAX_POINTS)
        rbw_values = list(np.linspace(start, stop, count))
        center_mhz = self._read("mxa-center", _positive_float, "Center frequency")
        span_mhz = self._read("mxa-span", _non_negative_float, "Span")
        return build_bandwidth_sweep(
            rbw_values,
            avg_count=self._read("mxa-averages", _positive_int, "Averages"),
            sweep_duration_ms=self._read("mxa-sweep-time", _positive_int, "Sweep time"),
            center_hz=center_mhz * 1e6,
            span_hz=span_mhz * 1e6,
            res_bw_hz=rbw_values[0],
            avg_type=self._selected("mxa-average-type", "Averaging type"),
            trig_source=self._selected("mxa-trigger-source", "Trigger source"),
        )

    def _build_frequency_run(self) -> Sweep:
        reference = self._selected("freq-reference", "Laser frequency source")
        if reference == "manual":
            center = self._read("freq-center", _positive_float, "Laser center")
        else:
            center = _RB_FREQUENCIES_THZ[reference]
        channel = self._read("freq-channel", _positive_int, "Wavemeter channel")
        points = self._read("freq-points", _positive_int, "Points", maximum=MAX_POINTS)
        step_mhz = self._read("freq-offset-mhz", _positive_float, "Offset step")
        settle_ms = self._read("freq-settle-time", _positive_int, "Laser settling time")
        step_thz = step_mhz * 1e-6
        offsets = [(i - (points - 1) / 2) * step_thz for i in range(points)]
        return build_frequency_sweep(
            laser_center_thz=center,
            wavemeter_channel=channel,
            offsets_thz=offsets,
            relax_time_s=settle_ms / 1000,
            sweep_duration_ms=DEFAULT_SWEEP_DURATION_MS,
        )

    # -- optional MXA read-back ---------------------------------------------

    def _start_read_mxa_settings(self) -> None:
        handle = self.iyzee_app.handles.get("mxa")
        if handle is None or handle.device is None:
            self.notify(
                "Connect the MXA first — press F1 for the Connect page.",
                severity="error",
            )
            return
        if self.iyzee_app.sweep_running:
            self.notify("Cannot read MXA settings during a running operation.", severity="error")
            return
        self.query_one("#read-mxa-settings", Button).disabled = True
        self._read_mxa_settings(handle.device, handle.lock)

    @work(thread=True, exclusive=True, group="mxa-settings", exit_on_error=False)
    def _read_mxa_settings(self, mx: Any, lock: Any) -> None:
        """Read MXA settings off the UI thread; disconnects are reported without breaking the page."""
        try:
            with lock:
                settings = {
                    "center_hz": mx.get_center_freq(),
                    "span_hz": mx.get_span(),
                    "avg_count": mx.get_average_count(),
                    "sweep_time_ms": mx.get_sweep_duration_ms(),
                    "avg_type": mx.get_average_type(),
                    "trig_source": mx.get_trigger_source(),
                }
        except Exception as exc:
            log.warning("sweep: could not read MXA settings: %s", exc)
            self._ui(self._finish_read_mxa_settings, settings=None, error=exc)
            return
        self._ui(self._finish_read_mxa_settings, settings=settings, error=None)

    def _finish_read_mxa_settings(
        self, *, settings: dict[str, Any] | None, error: Exception | None
    ) -> None:
        self.query_one("#read-mxa-settings", Button).disabled = False
        if error is not None:
            self.notify(
                f"Could not read settings from the MXA: {one_line(error)}",
                severity="error",
                markup=False,
            )
            return
        if settings is None:
            return
        self.query_one("#mxa-center", Input).value = f"{settings['center_hz'] / 1e6:g}"
        self.query_one("#mxa-span", Input).value = f"{settings['span_hz'] / 1e6:g}"
        self.query_one("#mxa-averages", Input).value = str(settings["avg_count"])
        self.query_one("#mxa-sweep-time", Input).value = str(settings["sweep_time_ms"])
        average_type = self.query_one("#mxa-average-type", Select)
        if settings["avg_type"] in {value for _, value in _MXA_AVERAGE_OPTIONS}:
            average_type.value = settings["avg_type"]
        trigger_source = self.query_one("#mxa-trigger-source", Select)
        if settings["trig_source"] in {value for _, value in _MXA_TRIGGER_OPTIONS}:
            trigger_source.value = settings["trig_source"]
        self.notify("Loaded the current MXA settings into the bandwidth form.")

    # -- the run itself, off the UI thread -----------------------------------

    @work(thread=True, exclusive=True, group="sweep", exit_on_error=False)
    def _run(self, runner: SweepRunner) -> None:
        # Nothing may leave a worker thread. Textual handles Exception (and cancellation) from a
        # worker but not a KeyboardInterrupt or SystemExit: those unwind the event loop and end
        # the whole app with no message, and a swallowed cancellation leaves the page stuck in
        # "running". Whatever it was, it becomes a failed run that is logged and shown.
        kind = runner.sweep.kind
        try:
            outcome = runner.run()
        except BaseException as exc:  # noqa: BLE001
            self._worker_died(kind, exc)
            return
        self._ui(self._finish, kind, aborted=outcome.aborted, setup_error=outcome.setup_error)

    def _worker_died(self, kind: str, exc: BaseException) -> None:
        log.critical("sweep: the worker thread died with %s", type(exc).__name__, exc_info=exc)
        if self._runner is not None:  # leave "failed" on disk, not "running"
            self._runner.mark_died()
        self._ui(self._finish, kind, aborted=False, setup_error=None, died=exc)

    def _on_phase(self, text: str) -> None:
        """What the run is doing now, from the worker thread."""
        self._ui(self._set_phase, text)

    def _set_phase(self, text: str) -> None:
        self._phase, self._phase_since = text, monotonic()
        self._show_phase()

    def _show_phase(self) -> None:
        """Show the stage and how long it has lasted, so a stall says where it is stalled."""
        line = (
            f"{escape(self._phase)} ({monotonic() - self._phase_since:.0f} s)"
            if self._phase
            else ""
        )
        self.query_one("#sweep-phase", Static).update(line)

    def _stop_phase(self) -> None:
        if self._phase_timer is not None:
            self._phase_timer.stop()
            self._phase_timer = None
        self._phase = ""
        self._show_phase()

    def _should_abort(self) -> bool:
        return self._abort_event.is_set() or self.iyzee_app.shutdown_requested.is_set()

    def _report_save_error(self, exc: Exception) -> None:
        """A failed save, reported once from the worker thread (the run itself carries on)."""
        self._ui(
            self.notify,
            f"Could not save results: {one_line(exc)}",
            severity="error",
            timeout=10,
            markup=False,
        )

    def _on_point(self, index, total, step, result, error) -> None:
        """Per point, from the worker thread, after it has been recorded and saved."""
        display_series = None
        if result is not None:
            now = monotonic()
            if now >= self._next_live_plot or index + 1 == total:
                difference = difference_series(
                    result.traces.get("squeezing"), result.traces.get("shot_noise"), result.label
                )
                if difference is not None:
                    display_series = prepare_series(*difference)
                self._next_live_plot = now + LIVE_PLOT_INTERVAL_S
        self._ui(self._on_step, index, total, step, result, error, display_series)

    def _on_step(
        self,
        index,
        total,
        step: Step,
        result: StepResult | None,
        error,
        display_series: tuple[list[float], list[float], str | None] | None,
    ) -> None:
        self.query_one("#sweep-progress", ProgressBar).update(progress=index + 1)
        log = self.query_one("#sweep-log", RichLog)
        label = getattr(step, "label", None) or f"step[{index}]"
        if error is not None:
            log.write(
                f"[{index + 1}/{total}] {escape(label)}: [red]failed[/red] ({escape(one_line(error))})"
            )
            return
        log.write(f"[{index + 1}/{total}] {escape(label)}: ok")
        if display_series is not None:
            plot = self.query_one("#sweep-plot", PlotextPlot)
            draw_series(plot, [display_series], clear=False)

    # -- "Capture trace": one raw spectrum off the analyzer, right now ------

    def _start_capture(self) -> None:
        mx_handle = self.iyzee_app.handles.get("mxa")
        if mx_handle is None:
            self.notify("Connect the MXA first — press F1 for the Connect page.", severity="error")
            return

        self.iyzee_app.sweep_running = True
        self.query_one("#run-sweep", Button).disabled = True
        self.query_one("#capture-trace", Button).disabled = True
        log = self.query_one("#sweep-log", RichLog)
        log.write("Capturing trace 1 from the analyzer…")
        self._capture(mx_handle.device)

    @work(thread=True, exclusive=True, group="capture", exit_on_error=False)
    def _capture(self, mx) -> None:
        try:
            self._capture_trace(mx)
        except BaseException as exc:
            log.critical(
                "capture: the worker thread died with %s", type(exc).__name__, exc_info=exc
            )
            self._ui(self._finish_capture, error=exc, freq=None, power=None)

    def _capture_trace(self, mx) -> None:
        # Deliberately does *not* call prepare_analyzer() first, unlike a
        # sweep — this captures whatever the analyzer is currently showing
        # (RBW, center freq, etc. left exactly as they are), not a
        # reconfigured measurement. A quick "is this actually working, and
        # what does the spectrum look like right now" check, not a
        # replacement for a real sweep. Trace 1 is this codebase's
        # convention for the primary trace (see procedures.TRACE_SQZ).
        with self.iyzee_app.handles["mxa"].lock:
            try:
                power = acquire_trace(mx, TRACE_SQZ)
                freq = mx.get_frequency_axis()
            except Exception as exc:
                log.exception("capture: failed to read a trace from the MXA")
                self._ui(self._finish_capture, error=exc, freq=None, power=None)
                return
        self._ui(self._finish_capture, error=None, freq=freq, power=power)

    def _finish_capture(
        self, *, error: BaseException | None, freq: list[float] | None, power: list[float] | None
    ) -> None:
        self.iyzee_app.sweep_running = False
        self.query_one("#run-sweep", Button).disabled = False
        self.query_one("#capture-trace", Button).disabled = False
        log = self.query_one("#sweep-log", RichLog)

        if error is not None:
            log.write(f"[red]Capture failed: {escape(one_line(error))}[/red]")
            self.notify(f"Capture failed: {one_line(error)}", severity="error", markup=False)
            return

        if freq is None or power is None:
            raise RuntimeError("successful trace capture returned no frequency or power data")
        plot = self.query_one("#sweep-plot", PlotextPlot)
        draw_series(
            plot,
            [(freq, power, "Trace 1")],
            title="Live trace capture",
            xlabel="Frequency (Hz)",
            ylabel="Power (dBm)",
        )
        log.write(f"Captured {len(power)} point(s).")

    def _finish(
        self,
        kind: str,
        *,
        aborted: bool,
        setup_error: Exception | None,
        died: BaseException | None = None,
    ) -> None:
        self._stop_phase()
        self.iyzee_app.sweep_running = False
        self.iyzee_app.session_note("idle")
        self.query_one("#run-sweep", Button).disabled = False
        self.query_one("#abort-sweep", Button).disabled = True
        self.query_one("#abort-sweep", Button).label = "Abort"
        self.query_one("#capture-trace", Button).disabled = False
        log = self.query_one("#sweep-log", RichLog)

        if setup_error is not None:
            log.write(f"[red]Could not start the sweep: {escape(one_line(setup_error))}[/red]")
            self.notify(
                f"Sweep could not start: {one_line(setup_error)}", severity="error", markup=False
            )
            return

        if died is not None:
            reason = type(died).__name__ + (f": {one_line(died)}" if str(died) else "")
            log.write(
                f"[red]The sweep stopped unexpectedly ({escape(reason)}). See the Log page.[/red]"
            )
            self.notify(
                f"Sweep stopped unexpectedly: {reason}", severity="error", timeout=15, markup=False
            )

        runner = self._runner
        if runner is None or not runner.collected:
            log.write("[yellow]No points recorded.[/yellow]")
            return

        if runner.save_path is None:
            # Every per-point save failed; one last attempt now that the run is over (this is
            # the UI thread, which is fine for one write; a failure is reported directly).
            runner.checkpoint(
                on_error=lambda exc: self.notify(
                    f"Could not save results: {one_line(exc)}",
                    severity="error",
                    timeout=10,
                    markup=False,
                )
            )
        path = runner.save_path
        collected = runner.collected
        self.iyzee_app.last_run = LastRun(kind=kind, results=list(collected), path=path)
        status = "aborted" if aborted else "stopped early" if died is not None else "finished"
        if path is None:
            log.write(
                f"[red]Sweep {status}: {len(collected)} point(s) recorded but "
                "could not be saved — they are available as `results` in the console.[/red]"
            )
            return
        log.write(f"Sweep {status}: {len(collected)} point(s) saved to {escape(str(path))}")
        self.notify(
            f"Saved {len(collected)} point(s) to {path.parent.name}/{path.name}",
            markup=False,
        )
