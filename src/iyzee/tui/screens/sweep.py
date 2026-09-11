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

import logging
import uuid
from dataclasses import asdict
from threading import Event

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
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
    bandwidth_sweep_steps,
    create_dirs,
    frequency_sweep_steps,
    prepare_analyzer,
    run_sequence,
    save_step_results,
)

log = logging.getLogger("iyzee.tui")


class SweepAborted(Exception):
    """Raised from inside the progress callback to stop a running sweep.

    ``run_sequence`` propagates exceptions raised by its ``on_step``
    callback immediately (see its docstring), which is what makes a clean
    "stop after the current step" abort possible without touching
    ``run_sequence`` itself.
    """


class SweepScreen(Screen):
    """Pick a sweep type, configure it, run it, and watch it live."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Sweep", classes="panel-title")
        yield Horizontal(
            Vertical(
                Label("Sweep type"),
                Select(
                    [("Bandwidth sweep", "bandwidth"), ("Frequency sweep", "frequency")],
                    value="bandwidth",
                    allow_blank=False,
                    id="sweep-type",
                ),
                Vertical(
                    Label("RBW start (Hz)"),
                    Input(value="20000", id="rbw-start"),
                    Label("RBW stop (Hz)"),
                    Input(value="380000", id="rbw-stop"),
                    Label("Steps"),
                    Input(value="19", id="rbw-steps"),
                    id="bw-fields",
                ),
                Vertical(
                    Label("Laser center (THz)"),
                    Input(value="377.1052067", id="freq-center"),
                    Label("Wavemeter channel"),
                    Input(value="1", id="freq-channel"),
                    Label("Points"),
                    Input(value="5", id="freq-points"),
                    Label("Offset step (kHz)"),
                    Input(value="10", id="freq-offset-khz"),
                    id="freq-fields",
                ),
            ),
            id="sweep-form",
        )
        yield Horizontal(
            Button("Run sweep", id="run-sweep", variant="success"),
            Button("Abort", id="abort-sweep", variant="error", disabled=True),
            id="sweep-controls",
        )
        yield ProgressBar(id="sweep-progress", total=1)
        yield PlotextPlot(id="sweep-plot")
        yield RichLog(id="sweep-log", highlight=False, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self._abort_event = Event()
        self._collected: list[StepResult] = []
        self.query_one("#freq-fields").display = False
        plot = self.query_one("#sweep-plot", PlotextPlot)
        plot.plt.title("Squeezing - shot noise")
        plot.plt.xlabel("Trace point")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "sweep-type":
            return
        self.query_one("#bw-fields").display = event.value == "bandwidth"
        self.query_one("#freq-fields").display = event.value == "frequency"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-sweep":
            self._start_sweep()
        elif event.button.id == "abort-sweep":
            self._abort_event.set()
            self.query_one("#abort-sweep", Button).disabled = True

    # -- kicking off the run -------------------------------------------------

    def _start_sweep(self) -> None:
        mx_handle = self.app.handles.get("mxa")
        if mx_handle is None:
            self.notify("Connect the MXA first (Connect screen).", severity="error")
            return

        kind = self.query_one("#sweep-type", Select).value
        try:
            if kind == "bandwidth":
                steps, config = self._build_bandwidth_run()
                shutter = None
            else:
                shutter_handle = self.app.handles.get("shutter")
                if shutter_handle is None or shutter_handle.shutter is None:
                    self.notify("Connect the shutter first (Connect screen).", severity="error")
                    return
                steps, config = self._build_frequency_run()
                shutter = shutter_handle.shutter
        except ValueError as exc:
            self.notify(f"Invalid sweep parameters: {exc}", severity="error")
            return

        self._abort_event = Event()
        self._collected = []
        log = self.query_one("#sweep-log", RichLog)
        log.clear()
        plot = self.query_one("#sweep-plot", PlotextPlot)
        plot.plt.clear_data()
        plot.refresh()
        progress = self.query_one("#sweep-progress", ProgressBar)
        progress.update(total=len(steps), progress=0)

        self.query_one("#run-sweep", Button).disabled = True
        self.query_one("#abort-sweep", Button).disabled = False
        self._run(mx_handle.device, shutter, steps, config, kind)

    def _build_bandwidth_run(self) -> tuple[list[Step], AnalyzerConfig]:
        start = _positive_float(self.query_one("#rbw-start", Input).value, "RBW start")
        stop = _positive_float(self.query_one("#rbw-stop", Input).value, "RBW stop")
        count = _positive_int(self.query_one("#rbw-steps", Input).value, "Steps")
        rbw_values = list(np.linspace(start, stop, count))
        config = AnalyzerConfig(
            center_hz=1e6, span_hz=0, avg_count=200, sweep_duration_ms=10, res_bw_hz=rbw_values[0]
        )
        return bandwidth_sweep_steps(rbw_values), config

    def _build_frequency_run(self) -> tuple[list[Step], AnalyzerConfig]:
        center = _positive_float(self.query_one("#freq-center", Input).value, "Laser center")
        channel = _positive_int(self.query_one("#freq-channel", Input).value, "Wavemeter channel")
        points = _positive_int(self.query_one("#freq-points", Input).value, "Points")
        step_khz = _positive_float(self.query_one("#freq-offset-khz", Input).value, "Offset step")
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
    def _run(self, mx, shutter, steps: list[Step], config: AnalyzerConfig, kind: str) -> None:
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
            self._ui(self._on_step, index, total, step, result, error)
            if self._abort_event.is_set():
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
            log.write(f"[{index + 1}/{total}] {label}: [red]failed[/red] ({error})")
            return
        log.write(f"[{index + 1}/{total}] {label}: ok")
        if result is not None:
            self._plot_result(result)

    def _plot_result(self, result: StepResult) -> None:
        squeezing = result.traces.get("squeezing")
        shot_noise = result.traces.get("shot_noise")
        if squeezing is None or shot_noise is None:
            return
        difference = np.asarray(squeezing) - np.asarray(shot_noise)
        plot = self.query_one("#sweep-plot", PlotextPlot)
        plot.plt.plot(list(range(len(difference))), list(difference), label=result.label)
        plot.refresh()

    def _finish(self, kind: str, *, aborted: bool, setup_error: Exception | None) -> None:
        self.query_one("#run-sweep", Button).disabled = False
        self.query_one("#abort-sweep", Button).disabled = True
        log = self.query_one("#sweep-log", RichLog)

        if setup_error is not None:
            log.write(f"[red]Could not configure the analyzer: {setup_error}[/red]")
            self.notify(f"Analyzer setup failed: {setup_error}", severity="error")
            return

        if not self._collected:
            log.write("[yellow]No points recorded.[/yellow]")
            return

        savedir = create_dirs(name=kind)
        path = save_step_results(self._collected, savedir)
        status = "aborted" if aborted else "finished"
        log.write(f"Sweep {status}: {len(self._collected)} point(s) saved to {path}")
        self.notify(f"Saved {len(self._collected)} point(s) to {path.name}")


def _positive_float(raw: str, field: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _positive_int(raw: str, field: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be a whole number") from exc
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value
