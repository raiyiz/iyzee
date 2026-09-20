"""Sweep screen: configure and run a bandwidth or frequency sweep.

This deliberately does *not* call the convenience
``run_bandwidth_sweep``/``run_frequency_sweep`` wrappers in
``experiment.procedures`` — those don't take a progress callback. Instead
it composes the same lower-level pieces they're built from
(``AnalyzerConfig``, ``prepare_analyzer``, ``ExperimentContext``,
``run_sequence``) directly, which is exactly what that layer is meant for:
a new caller with different needs (here, live progress) is a few lines
against the existing building blocks, not a change to ``experiment/``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from functools import partial
from threading import Event
from typing import TYPE_CHECKING, cast

import numpy as np
from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual_plotext import PlotextPlot

from ...experiment import (
    TRACE_SHOT,
    TRACE_SQZ,
    AnalyzerConfig,
    ExperimentContext,
    Step,
    StepResult,
    acquire_trace,
    bandwidth_sweep_steps,
    create_dirs,
    difference_series,
    frequency_sweep_steps,
    prepare_analyzer,
    run_sequence,
    save_step_results,
)
from ..instruments import ShutterHandle, _VisaHandle
from ..plotting import draw_series
from ..text import one_line
from ..workers import LastRun
from .page import Page

if TYPE_CHECKING:
    from pathlib import Path

    from ..app import IyzeeApp

log = logging.getLogger("iyzee.tui")


# Upper bound on "Steps" / "Points". Each step is a full analyzer averaging
# run (seconds), so 1000 is already a multi-minute measurement; the cap
# exists to turn a stray extra zero (or 10**9) into a clear message instead
# of a frozen UI and gigabytes of linspace.
MAX_POINTS = 1000


class FieldError(ValueError):
    """A form field failed validation; ``field_id`` says which one.

    A ValueError subclass so callers that only care *that* the form is
    invalid keep working, while the screen can point at the offending field.
    """

    def __init__(self, field_id: str, message: str) -> None:
        super().__init__(message)
        self.field_id = field_id


def _field(label: str, widget: Widget, *, id: str | None = None) -> Vertical:
    """A label stacked over its input, as one grid cell of the sweep form.

    Grouping the pair in a single container is what lets ``app.tcss``
    reflow the form as a grid (1/2/4 columns depending on width) without
    ever separating a label from the field it belongs to.
    """
    return Vertical(Label(label), widget, classes="field", id=id)


class SweepAborted(Exception):
    """Raised from inside the progress callback to stop a running sweep.

    ``run_sequence`` propagates exceptions raised by its ``on_step``
    callback immediately (see its docstring), which is what makes a clean
    "stop after the current step" abort possible without touching
    ``run_sequence`` itself.
    """


class SweepScreen(Page):
    """Pick a sweep type, configure it, run it, and watch it live.

    The form, controls, plot and log together are taller than a small
    terminal, so this is a scrolling ``Page`` rather than a plain
    ``Vertical`` (which silently clips whatever doesn't fit — that is how
    the Run/Abort buttons ended up unreachable). The responsive reflow
    itself lives in ``app.tcss`` (see ``IyzeeApp.HORIZONTAL_BREAKPOINTS``).
    """

    @property
    def iyzee_app(self) -> IyzeeApp:
        """``self.app`` narrowed to the concrete app type (see ConnectScreen.iyzee_app)."""
        return cast("IyzeeApp", self.app)

    def compose(self) -> ComposeResult:
        yield Static("Sweep", classes="panel-title")
        # Shown only while something the selected sweep needs isn't
        # connected; see refresh_readiness().
        yield Static("", id="sweep-status")
        with Vertical(id="sweep-form"):
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
            with Grid(id="freq-fields", classes="field-grid"):
                yield _field("Laser center (THz)", Input(value="377.1052067", id="freq-center"))
                yield _field("Wavemeter channel", Input(value="1", id="freq-channel"))
                yield _field("Points", Input(value="5", id="freq-points"))
                yield _field("Offset step (kHz)", Input(value="10", id="freq-offset-khz"))
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

    def on_input_changed(self, event: Input.Changed) -> None:
        # Editing a field you were told is wrong clears the marker.
        event.input.remove_class("-invalid")

    # -- readiness ---------------------------------------------------------

    def _missing_instruments(self, kind: object) -> list[str]:
        """Names of the instruments the given sweep needs that aren't connected."""
        handles = self.iyzee_app.handles
        missing: list[str] = []
        if "mxa" not in handles:
            missing.append("MXA")
        if kind == "frequency":
            shutter = handles.get("shutter")
            if shutter is None or cast(ShutterHandle, shutter).shutter is None:
                missing.append("shutter")
        return missing

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
                + " and the ".join(missing)
                + " first — press F1 for the Connect page."
            )
        status.display = bool(missing)

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
        mx_handle = self.iyzee_app.handles.get("mxa")
        if mx_handle is None:
            self.notify("Connect the MXA first — press F1 for the Connect page.", severity="error")
            return
        # The Connect screen always builds "mxa" from a _VisaHandle and
        # "shutter" from a ShutterHandle (see instruments.INSTRUMENTS) —
        # handles is typed dict[str, InstrumentHandle] because that's all
        # the Connect screen itself needs, but this screen needs the
        # concrete wrapper's .device/.shutter, which aren't on that
        # narrower Protocol.
        mx_handle = cast(_VisaHandle, mx_handle)

        kind = self.query_one("#sweep-type", Select).value
        try:
            if kind == "bandwidth":
                steps, config = self._build_bandwidth_run()
                shutter = None
            else:
                shutter_handle = self.iyzee_app.handles.get("shutter")
                if shutter_handle is None or cast(ShutterHandle, shutter_handle).shutter is None:
                    self.notify(
                        "Connect the shutter first — press F1 for the Connect page.",
                        severity="error",
                    )
                    return
                steps, config = self._build_frequency_run()
                shutter = cast(ShutterHandle, shutter_handle).shutter
        except FieldError as exc:
            self._flag_invalid(exc.field_id)
            self.notify(f"Invalid sweep parameters: {exc}", severity="error", markup=False)
            return
        except ValueError as exc:
            self.notify(f"Invalid sweep parameters: {exc}", severity="error", markup=False)
            return

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
        progress.update(total=len(steps), progress=0)

        self.query_one("#run-sweep", Button).disabled = True
        self.query_one("#abort-sweep", Button).disabled = False
        self.query_one("#capture-trace", Button).disabled = True
        self.iyzee_app.sweep_running = True
        self._run(mx_handle.device, shutter, steps, config, kind)

    def _flag_invalid(self, field_id: str) -> None:
        """Mark one input as wrong and put the cursor in it, ready to fix."""
        for widget in self.query("Input.-invalid"):
            widget.remove_class("-invalid")
        widget = self.query_one(f"#{field_id}", Input)
        widget.add_class("-invalid")
        widget.focus()

    def _read(self, field_id: str, parse, label: str, **kwargs):
        """Parse one form field, tagging any failure with the field's id."""
        try:
            return parse(self.query_one(f"#{field_id}", Input).value, label, **kwargs)
        except ValueError as exc:
            raise FieldError(field_id, str(exc)) from exc

    def _build_bandwidth_run(self) -> tuple[Sequence[Step], AnalyzerConfig]:
        start = self._read("rbw-start", _positive_float, "RBW start")
        stop = self._read("rbw-stop", _positive_float, "RBW stop")
        count = self._read("rbw-steps", _positive_int, "Steps", maximum=MAX_POINTS)
        rbw_values = list(np.linspace(start, stop, count))
        config = AnalyzerConfig(
            center_hz=1e6, span_hz=0, avg_count=200, sweep_duration_ms=10, res_bw_hz=rbw_values[0]
        )
        return bandwidth_sweep_steps(rbw_values), config

    def _build_frequency_run(self) -> tuple[Sequence[Step], AnalyzerConfig]:
        center = self._read("freq-center", _positive_float, "Laser center")
        channel = self._read("freq-channel", _positive_int, "Wavemeter channel")
        points = self._read("freq-points", _positive_int, "Points", maximum=MAX_POINTS)
        step_khz = self._read("freq-offset-khz", _positive_float, "Offset step")
        step_thz = step_khz * 1e-9
        offsets = [(i - (points // 2)) * step_thz for i in range(points)]

        config = AnalyzerConfig(
            center_hz=1.5e6, span_hz=0, avg_count=150, sweep_duration_ms=10, res_bw_hz=24e3
        )
        relax_time_s = config.sweep_duration_ms * config.avg_count / 1000
        steps = frequency_sweep_steps(
            laser_center_thz=center,
            wavemeter_channel=channel,
            relax_time_s=relax_time_s,
            offsets_thz=offsets,
        )
        return steps, config

    # -- the run itself, off the UI thread -----------------------------------

    def _ui(self, callback, *args, **kwargs) -> None:
        """Call back into the UI thread from the sweep worker, without
        letting a now-stale widget reference (e.g. the user switched away
        from this screen mid-sweep) turn into an unhandled worker
        exception. See ``ConnectScreen._ui`` for the same pattern.
        """
        try:
            self.app.call_from_thread(callback, *args, **kwargs)
        except Exception:
            log.exception("sweep screen: UI update from worker thread failed")

    @work(thread=True, exclusive=True, group="sweep", exit_on_error=False)
    def _run(self, mx, shutter, steps: Sequence[Step], config: AnalyzerConfig, kind: str) -> None:
        # Hold the MXA's lock (and the shutter's, for a frequency sweep) for
        # the whole run, not just individual calls — a sweep is one logical
        # operation, and interleaving a console cell's commands partway
        # through it would be just as broken as interleaving two sweeps.
        # This is the same lock instruments.LockedProxy acquires per call
        # for console code, and ConnectScreen acquires around connect/
        # disconnect (see IyzeeApp.instrument_locks).
        locks = [self.iyzee_app.instrument_locks["mxa"]]
        if shutter is not None:
            locks.append(self.iyzee_app.instrument_locks["shutter"])

        with contextlib.ExitStack() as stack:
            for lock in locks:
                stack.enter_context(lock)

            try:
                prepare_analyzer(mx, (TRACE_SQZ, TRACE_SHOT), config)
            except Exception as exc:  # noqa: BLE001
                log.exception("sweep: failed to configure analyzer")
                self._ui(self._finish, kind, aborted=False, setup_error=exc)
                return

            run_id = uuid.uuid4().hex[:8]
            ctx = ExperimentContext(mx=mx, run_id=run_id, shutter=shutter, config=asdict(config))

            def on_step(index, total, step, result, error) -> None:
                if result is not None:
                    self._collected.append(result)
                    # Straight to disk, before anything else: a crash, a
                    # power cut or a quit part-way through a long run then
                    # costs at most the step in flight, not the whole run.
                    self._checkpoint(kind)
                self._ui(self._on_step, index, total, step, result, error)
                if self._abort_event.is_set() or self.iyzee_app.shutdown_requested.is_set():
                    raise SweepAborted()

            aborted = False
            try:
                run_sequence(steps, ctx, on_error="skip", on_step=on_step)
            except SweepAborted:
                aborted = True
            except Exception:  # noqa: BLE001 - last-resort net; specific errors are
                # already handled per-step by run_sequence(on_error="skip") plus
                # on_step above, so anything reaching here is unexpected.
                log.exception("sweep: run_sequence raised unexpectedly")

        self._ui(self._finish, kind, aborted=aborted, setup_error=None)

    def _on_step(self, index, total, step: Step, result: StepResult | None, error) -> None:
        self.query_one("#sweep-progress", ProgressBar).update(progress=index + 1)
        log = self.query_one("#sweep-log", RichLog)
        label = getattr(step, "label", None) or f"step[{index}]"
        if error is not None:
            log.write(
                f"[{index + 1}/{total}] {escape(label)}: [red]failed[/red] ({escape(one_line(error))})"
            )
            return
        log.write(f"[{index + 1}/{total}] {escape(label)}: ok")
        if result is not None:
            self._plot_result(result)

    def _plot_result(self, result: StepResult) -> None:
        series = difference_series(
            result.traces.get("squeezing"), result.traces.get("shot_noise"), result.label
        )
        if series is None:
            return
        plot = self.query_one("#sweep-plot", PlotextPlot)
        draw_series(plot, [series], clear=False)

    # -- "Capture trace": one raw spectrum off the analyzer, right now ------

    def _start_capture(self) -> None:
        mx_handle = self.iyzee_app.handles.get("mxa")
        if mx_handle is None:
            self.notify("Connect the MXA first — press F1 for the Connect page.", severity="error")
            return
        mx_handle = cast(_VisaHandle, mx_handle)

        self.iyzee_app.sweep_running = True
        self.query_one("#run-sweep", Button).disabled = True
        self.query_one("#capture-trace", Button).disabled = True
        log = self.query_one("#sweep-log", RichLog)
        log.write("Capturing trace 1 from the analyzer…")
        self._capture(mx_handle.device)

    @work(thread=True, exclusive=True, group="capture", exit_on_error=False)
    def _capture(self, mx) -> None:
        # Deliberately does *not* call prepare_analyzer() first, unlike a
        # sweep — this captures whatever the analyzer is currently showing
        # (RBW, center freq, etc. left exactly as they are), not a
        # reconfigured measurement. A quick "is this actually working, and
        # what does the spectrum look like right now" check, not a
        # replacement for a real sweep. Trace 1 is this codebase's
        # convention for the primary trace (see procedures.TRACE_SQZ).
        with self.iyzee_app.instrument_locks["mxa"]:
            try:
                power = acquire_trace(mx, TRACE_SQZ)
                freq = mx.get_frequency_axis()
            except Exception as exc:  # noqa: BLE001
                log.exception("capture: failed to read a trace from the MXA")
                self._ui(self._finish_capture, error=exc, freq=None, power=None)
                return
        self._ui(self._finish_capture, error=None, freq=freq, power=power)

    def _finish_capture(
        self, *, error: Exception | None, freq: list[float] | None, power: list[float] | None
    ) -> None:
        self.iyzee_app.sweep_running = False
        self.query_one("#run-sweep", Button).disabled = False
        self.query_one("#capture-trace", Button).disabled = False
        log = self.query_one("#sweep-log", RichLog)

        if error is not None:
            log.write(f"[red]Capture failed: {escape(one_line(error))}[/red]")
            self.notify(f"Capture failed: {one_line(error)}", severity="error", markup=False)
            return

        assert freq is not None and power is not None  # error is None guarantees both are set
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
        self._savedir: Path | None = None
        self._save_path: Path | None = None
        self._save_warned = False

    def _checkpoint(self, kind: str, *, on_ui_thread: bool = False) -> None:
        """Write everything collected so far to disk.

        Called after every recorded point, from the worker thread. The same
        file is overwritten each time (atomically — see ``save_data``), so
        a run leaves exactly one archive, which is complete when the run
        ends and merely shorter if it doesn't. A failing save (disk full,
        permissions) must never abort the measurement itself: it is
        logged, and reported once rather than once per point.

        ``on_ui_thread`` is for the one call made from ``_finish``:
        ``call_from_thread`` raises if used from the UI thread itself, so
        that path has to notify directly.
        """
        try:
            if self._savedir is None:
                self._savedir = create_dirs(name=kind)
            self._save_path = save_step_results(
                list(self._collected), self._savedir, path=self._save_path
            )
        except Exception as exc:  # noqa: BLE001
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

    def _finish(self, kind: str, *, aborted: bool, setup_error: Exception | None) -> None:
        self.iyzee_app.sweep_running = False
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

        if not self._collected:
            log.write("[yellow]No points recorded.[/yellow]")
            return

        if self._save_path is None:
            # Every per-point save failed; one last attempt now that the
            # run is over (this is the UI thread, which is fine for one write).
            self._checkpoint(kind, on_ui_thread=True)
        path = self._save_path
        self.iyzee_app.last_run = LastRun(kind=kind, results=list(self._collected), path=path)
        status = "aborted" if aborted else "finished"
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


def _positive_float(raw: str, field: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    # float() happily parses "nan" and "inf"; neither is a usable setting,
    # and nan even slips past a `<= 0` check because it compares false.
    if not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _positive_int(raw: str, field: str, *, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a whole number") from exc
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value
