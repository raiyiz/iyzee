"""Sweep screen: configure and run a bandwidth or frequency sweep.

The shared ``experiment.runner`` owns analyzer setup, whole-run locks,
per-point checkpoints, and final run status. This screen keeps only the
Textual-specific progress display, live plot, and abort signal.

Guide: :guide:`sec-pages`
"""

from __future__ import annotations

import contextlib
import logging
from functools import partial
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING, Any

import numpy as np
from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import (
    Button,
    Input,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

from ...experiment import (
    DEFAULT_SWEEP_DURATION_MS,
    FREQUENCY_STEP_THZ,
    SWEEP_KINDS,
    TRACE_SQZ,
    RunRecord,
    Step,
    StepResult,
    Sweep,
    acquire_trace,
    build_bandwidth_sweep,
    build_frequency_sweep,
    create_dirs,
    difference_series,
    save_step_results,
)
from ...experiment.runner import run_recorded_sweep
from ..commands import Command
from ..plotting import draw_series, prepare_series
from ..text import one_line
from ..workers import LastRun
from .page import FieldError, Page, _field, _positive_float, _positive_int

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger("iyzee.tui")


# Upper bound on "Steps" / "Points". Each step is a full analyzer averaging
# run (seconds), so 1000 is already a multi-minute measurement; the cap
# exists to turn a stray extra zero (or 10**9) into a clear message instead
# of a frozen UI and gigabytes of linspace.
MAX_POINTS = 1000
LIVE_PLOT_INTERVAL_S = 0.1

# How each instrument key a sweep ``requires`` is named in "connect the ..." messages.
_INSTRUMENT_NAMES = {"mxa": "MXA", "shutter": "shutter", "wavemeter": "wavemeter"}


class SweepScreen(Page):
    _savedir: Path | None
    _save_path: Path | None
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
                yield _field(
                    "Sweep duration (ms)",
                    Input(value=str(DEFAULT_SWEEP_DURATION_MS), id="sweep-duration"),
                )
            with Grid(id="bw-fields", classes="field-grid"):
                yield _field("RBW start (Hz)", Input(value="20000", id="rbw-start"))
                yield _field("RBW stop (Hz)", Input(value="380000", id="rbw-stop"))
                yield _field("Steps", Input(value="19", id="rbw-steps"))
            with Grid(id="freq-fields", classes="field-grid"):
                yield _field("Laser center (THz)", Input(value="377.1052067", id="freq-center"))
                yield _field("Wavemeter channel", Input(value="4", id="freq-channel"))
                yield _field("Points", Input(value="5", id="freq-points"))
                yield _field(
                    "Offset step (kHz)",
                    Input(value=f"{FREQUENCY_STEP_THZ * 1e9:g}", id="freq-offset-khz"),
                )
        with Grid(id="sweep-controls"):
            yield Button("Run sweep", id="run-sweep", variant="success")
            yield Button("Abort", id="abort-sweep", variant="error", disabled=True)
            yield Button("Capture trace", id="capture-trace")
        yield ProgressBar(id="sweep-progress", total=1)
        yield PlotextPlot(id="sweep-plot")
        yield RichLog(id="sweep-log", highlight=False, markup=True)

    def on_mount(self) -> None:
        self._abort_event = Event()
        self._collected: list[StepResult] = []
        # How the current run is going/ended; saved as the file's run_metadata.
        self._record: RunRecord | None = None
        self._reset_checkpoint()
        self.query_one("#freq-fields").display = False
        plot = self.query_one("#sweep-plot", PlotextPlot)
        plot.plt.title("Squeezing - shot noise")
        plot.plt.xlabel("Trace point")
        plot.plt.ylabel("Squeezing - shot noise")
        self.refresh_readiness()

    def on_show(self) -> None:
        self.refresh_readiness()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "sweep-type":
            return
        self.query_one("#bw-fields").display = event.value == "bandwidth"
        self.query_one("#freq-fields").display = event.value == "frequency"
        self.refresh_readiness()

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

    def _request_abort(self) -> None:
        """Abort takes effect at the next step boundary, not instantly — a
        step is one analyzer averaging run and can't be interrupted. So
        acknowledge the click visibly, otherwise a silently greyed-out
        button looks like nothing happened."""
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
        self._collected = []
        self._reset_checkpoint()
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
        self._run(sweep, devices)

    def _build_bandwidth_run(self) -> Sweep:
        start = self._read("rbw-start", _positive_float, "RBW start")
        stop = self._read("rbw-stop", _positive_float, "RBW stop")
        count = self._read("rbw-steps", _positive_int, "Steps", maximum=MAX_POINTS)
        rbw_values = list(np.linspace(start, stop, count))
        return build_bandwidth_sweep(
            rbw_values, sweep_duration_ms=self._read_duration(), res_bw_hz=rbw_values[0]
        )

    def _build_frequency_run(self) -> Sweep:
        center = self._read("freq-center", _positive_float, "Laser center")
        channel = self._read("freq-channel", _positive_int, "Wavemeter channel")
        points = self._read("freq-points", _positive_int, "Points", maximum=MAX_POINTS)
        step_khz = self._read("freq-offset-khz", _positive_float, "Offset step")
        step_thz = step_khz * 1e-9
        offsets = [(i - (points - 1) / 2) * step_thz for i in range(points)]
        return build_frequency_sweep(
            laser_center_thz=center,
            wavemeter_channel=channel,
            offsets_thz=offsets,
            sweep_duration_ms=self._read_duration(),
        )

    def _read_duration(self) -> int:
        return self._read("sweep-duration", _positive_int, "Sweep duration")

    # -- the run itself, off the UI thread -----------------------------------

    @work(thread=True, exclusive=True, group="sweep", exit_on_error=False)
    def _run(self, sweep: Sweep, devices: dict[str, Any]) -> None:
        # Nothing may leave a worker thread. Textual handles Exception (and cancellation) from a
        # worker but not a KeyboardInterrupt or SystemExit: those unwind the event loop and end
        # the whole app with no message, and a swallowed cancellation leaves the page stuck in
        # "running". Whatever it was, it becomes a failed run that is logged and shown.
        try:
            self._run_sweep(sweep, devices)
        except BaseException as exc:  # noqa: BLE001
            self._worker_died(sweep.kind, exc)

    def _worker_died(self, kind: str, exc: BaseException) -> None:
        log.critical("sweep: the worker thread died with %s", type(exc).__name__, exc_info=exc)
        if self._record is not None:  # leave "failed" on disk, not "running"
            with contextlib.suppress(Exception):
                self._record.finish(aborted=False, failed=True)
                if self._collected:
                    self._checkpoint(kind)
        self._ui(self._finish, kind, aborted=False, setup_error=None, died=exc)

    def _run_sweep(self, sweep: Sweep, devices: dict[str, Any]) -> None:
        kind = sweep.kind
        next_live_plot = monotonic()

        def on_step(
            index: int,
            total: int,
            step: Step,
            result: StepResult | None,
            error: BaseException | None,
        ) -> None:
            nonlocal next_live_plot
            display_series = None
            if result is not None:
                now = monotonic()
                if now >= next_live_plot or index + 1 == total:
                    difference = difference_series(
                        result.traces.get("squeezing"),
                        result.traces.get("shot_noise"),
                        result.label,
                    )
                    if difference is not None:
                        display_series = prepare_series(*difference)
                    next_live_plot = now + LIVE_PLOT_INTERVAL_S
            self._ui(self._on_step, index, total, step, result, error, display_series)

        def on_record(record: RunRecord) -> None:
            self._record = record

        def on_checkpoint(savedir: Path, path: Path) -> None:
            # Keep the live screen state in sync so a worker-level failure can
            # finalize the same archive instead of creating a second one.
            self._savedir = savedir
            self._save_path = path

        def on_save_error(exc: Exception) -> None:
            self._save_warned = True
            self._ui(
                self.notify,
                f"Could not save results: {one_line(exc)}",
                severity="error",
                timeout=10,
                markup=False,
            )

        outcome = run_recorded_sweep(
            sweep,
            mx=devices["mxa"],
            locks=[self.iyzee_app.handles[key].lock for key in sweep.requires],
            shutter=devices.get("shutter"),
            wavemeter=devices.get("wavemeter"),
            results=self._collected,
            on_step=on_step,
            should_abort=lambda: (
                self._abort_event.is_set() or self.iyzee_app.shutdown_requested.is_set()
            ),
            on_record=on_record,
            on_checkpoint=on_checkpoint,
            on_save_error=on_save_error,
        )
        self._savedir = outcome.savedir or self._savedir
        self._save_path = outcome.save_path or self._save_path
        if outcome.setup_error is not None:
            self._ui(self._finish, kind, aborted=False, setup_error=outcome.setup_error)
            return
        self._ui(
            self._finish,
            kind,
            aborted=outcome.aborted,
            setup_error=None,
            died=outcome.error,
        )

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

    # -- checkpointing ----------------------------------------------------

    def _reset_checkpoint(self) -> None:
        self._savedir = None
        self._save_path = None
        self._save_warned = False
        self._record = None

    def _checkpoint(self, kind: str, *, on_ui_thread: bool = False) -> None:
        """Write everything collected so far to disk.

        Called after every recorded point, from the worker thread. The same
        file pair is overwritten each time (atomically — see
        ``save_step_results``), so a run leaves exactly one archive, which
        is complete when the run ends and merely shorter if it doesn't. A
        failing save (disk full, permissions) must never abort the
        measurement itself: it is logged, and reported once rather than
        once per point.

        ``on_ui_thread`` is for the one call made from ``_finish``:
        ``call_from_thread`` raises if used from the UI thread itself, so
        that path has to notify directly.
        """
        try:
            if self._savedir is None:
                self._savedir = create_dirs()
            self._save_path = save_step_results(
                list(self._collected),
                self._savedir,
                self._record.as_metadata() if self._record is not None else None,
                name=kind,
                path=self._save_path,
            )
        except Exception as exc:
            log.exception("sweep: could not save results")
            if not self._save_warned:
                self._save_warned = True
                notify = self.notify if on_ui_thread else partial(self._ui, self.notify)
                notify(
                    f"Could not save results: {one_line(exc)}",
                    severity="error",
                    timeout=10,
                    markup=False,
                )

    def _finish(
        self,
        kind: str,
        *,
        aborted: bool,
        setup_error: Exception | None,
        died: BaseException | None = None,
    ) -> None:
        self.iyzee_app.sweep_running = False
        self.iyzee_app.session_note("idle")
        self.query_one("#run-sweep", Button).disabled = False
        self.query_one("#abort-sweep", Button).disabled = True
        self.query_one("#abort-sweep", Button).label = "Abort"
        self.query_one("#capture-trace", Button).disabled = False
        log = self.query_one("#sweep-log", RichLog)

        if setup_error is not None:
            log.write(
                f"[red]Could not configure the analyzer: {escape(one_line(setup_error))}[/red]"
            )
            self.notify(
                f"Analyzer setup failed: {one_line(setup_error)}", severity="error", markup=False
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

        if not self._collected:
            log.write("[yellow]No points recorded.[/yellow]")
            return

        if self._save_path is None:
            # Every per-point save failed; one last attempt now that the
            # run is over (this is the UI thread, which is fine for one write).
            self._checkpoint(kind, on_ui_thread=True)
        path = self._save_path
        self.iyzee_app.last_run = LastRun(kind=kind, results=list(self._collected), path=path)
        status = "aborted" if aborted else "stopped early" if died is not None else "finished"
        if path is None:
            log.write(
                f"[red]Sweep {status}: {len(self._collected)} point(s) recorded but "
                "could not be saved — they are available as `results` in the console.[/red]"
            )
            return
        log.write(f"Sweep {status}: {len(self._collected)} point(s) saved to {escape(str(path))}")
        self.notify(
            f"Saved {len(self._collected)} point(s) to {path.parent.name}/{path.name}",
            markup=False,
        )
