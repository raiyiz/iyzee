"""Interactive terminal application for common laboratory workflows."""

from __future__ import annotations

import logging
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, ProgressBar, Static

from ..experiment import capture_traces, create_dirs, run_bandwidth_sweep, run_frequency_sweep, save_step_results
from ..experiment.step import StepResult
from .devices import DeviceManager
from .trace_plot import TracePlot

log = logging.getLogger("iyzee.tui")


class IyzTuiApp(App[None]):
    """Small, keyboard-friendly control surface for experiment runs."""

    TITLE = "iyzee — laboratory control"
    SUB_TITLE = "connect → configure → acquire → review"

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #workspace {
        height: 1fr;
        padding: 1 2;
    }

    #sidebar {
        width: 34;
        min-width: 28;
        padding: 1;
        border: round $primary;
    }

    #main {
        width: 1fr;
        padding: 0 1 0 2;
    }

    .section-title {
        text-style: bold;
        margin: 0 0 1 0;
    }

    .device-row {
        height: auto;
        margin: 0 0 1 0;
    }

    .status {
        width: 1fr;
        padding: 0 1;
    }

    .control-row {
        height: auto;
        margin: 0 0 1 0;
    }

    .control-row Input {
        width: 1fr;
        margin-right: 1;
    }

    Button {
        margin: 0 1 1 0;
    }

    #run-status {
        height: 3;
        padding: 0 1;
        border: round $secondary;
        margin-bottom: 1;
    }

    #progress {
        margin-bottom: 1;
    }

    #plot {
        height: 1fr;
        border: round $primary;
        min-height: 20;
    }

    #log {
        height: 6;
        overflow-y: auto;
        border: round $secondary;
        padding: 1;
    }

    .muted {
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("b", "bandwidth", "Bandwidth sweep"),
        ("f", "frequency", "Frequency sweep"),
        ("c", "capture", "Capture traces"),
        ("d", "disconnect", "Disconnect"),
        ("q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.devices = DeviceManager()
        self._running = False
        self._last_results: list[StepResult] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                yield Label("DEVICES", classes="section-title")
                with Horizontal(classes="device-row"):
                    yield Static("○ MXA", id="mxa-status", classes="status")
                    yield Button("Connect", id="connect-mxa", variant="primary")
                with Horizontal(classes="device-row"):
                    yield Static("○ Shutter / PSU", id="shutter-status", classes="status")
                    yield Button("Connect", id="connect-shutter", variant="primary")

                yield Label("RUN CONTROLS", classes="section-title")
                with Horizontal(classes="control-row"):
                    yield Button("Bandwidth sweep", id="bandwidth", variant="success")
                    yield Button("Frequency sweep", id="frequency", variant="success")
                with Horizontal(classes="control-row"):
                    yield Button("Capture traces", id="capture", variant="warning")
                    yield Button("Disconnect", id="disconnect")

                yield Label("Frequency defaults", classes="section-title")
                with Horizontal(classes="control-row"):
                    yield Input("377.1052067", id="frequency-center", type="number")
                    yield Input("1", id="wavemeter-channel", type="integer")

                yield Static(
                    "B / F / C run workflows • D disconnects all devices • Q quits safely",
                    classes="muted",
                )

            with Vertical(id="main"):
                yield Static("Ready", id="run-status")
                yield ProgressBar(total=1, show_eta=False, id="progress")
                yield TracePlot(id="plot")
                yield Static("No run yet.", id="log")

        yield Footer()

    def _set_status(self, widget_id: str, text: str) -> None:
        self.query_one(f"#{widget_id}", Static).update(text)

    def _log(self, message: str) -> None:
        self.query_one("#log", Static).update(message)
        log.info(message)

    def _set_running(self, running: bool) -> None:
        self._running = running
        for button_id in (
            "connect-mxa",
            "connect-shutter",
            "bandwidth",
            "frequency",
            "capture",
            "disconnect",
        ):
            self.query_one(f"#{button_id}", Button).disabled = running

    def _start_run(self, total: int, label: str) -> None:
        self._set_running(True)
        progress = self.query_one("#progress", ProgressBar)
        progress.update(total=total, progress=0)
        self.query_one("#run-status", Static).update(label)
        self._log(label)

    def _step_finished(self, index: int, total: int, result: StepResult) -> None:
        progress = self.query_one("#progress", ProgressBar)
        progress.update(total=total, progress=index)
        self._last_results.append(result)
        self.query_one("#plot", TracePlot).update_result(result)
        self.query_one("#run-status", Static).update(f"{index}/{total}  {result.label}")

    def _run_finished(self, results: list[StepResult], run_name: str) -> None:
        self._last_results = results
        self._set_running(False)
        self.query_one("#progress", ProgressBar).update(
            total=max(len(results), 1), progress=len(results)
        )
        self.query_one("#run-status", Static).update(f"Finished — {len(results)} points")
        savedir = create_dirs(run_name)
        path = save_step_results(results, savedir, run_metadata={"workflow": run_name})
        self._log(f"Saved {len(results)} points → {Path(path).name}")

    def _run_failed(self, error: Exception) -> None:
        self._set_running(False)
        self.query_one("#run-status", Static).update("Run failed")
        self._log(f"ERROR: {error}")

    @work(thread=True)
    def _connect_mxa_worker(self) -> None:
        try:
            self.devices.connect_mxa()
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
        else:
            self.call_from_thread(self._set_status, "mxa-status", "● MXA connected")
            self.call_from_thread(self._log, "MXA connected")

    @work(thread=True)
    def _connect_shutter_worker(self) -> None:
        try:
            self.devices.connect_shutter()
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
        else:
            self.call_from_thread(self._set_status, "shutter-status", "● Shutter / PSU connected")
            self.call_from_thread(self._log, "Shutter / PSU connected")

    @work(thread=True)
    def _capture_worker(self) -> None:
        try:
            with self.devices.mxa_for_operation() as mx:
                result = capture_traces(mx)
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
            return

        self.call_from_thread(self._step_finished, 1, 1, result)
        self.call_from_thread(self._run_finished, [result], "single-capture")

    @work(thread=True)
    def _bandwidth_worker(self) -> None:
        try:
            with self.devices.mxa_for_operation() as mx:
                self.call_from_thread(self._start_run, 19, "Running bandwidth sweep…")
                results = run_bandwidth_sweep(mx, on_step=self._on_worker_step)
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
            return
        self.call_from_thread(self._run_finished, results, "bandwidth-sweep")

    @work(thread=True)
    def _frequency_worker(self, center_thz: float, channel: int) -> None:
        try:
            with self.devices.frequency_sweep_devices() as (mx, shutter):
                self.call_from_thread(self._start_run, 2, "Running frequency sweep…")
                results = run_frequency_sweep(
                    mx,
                    shutter,
                    laser_center_thz=center_thz,
                    wavemeter_channel=channel,
                    on_step=self._on_worker_step,
                )
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
            return
        self.call_from_thread(self._run_finished, results, "frequency-sweep")

    def _on_worker_step(self, index: int, total: int, result: StepResult) -> None:
        """Bridge an experiment-thread progress event back to Textual's UI thread."""
        self.call_from_thread(self._step_finished, index, total, result)

    @work(thread=True)
    def _disconnect_worker(self) -> None:
        try:
            self.devices.close_all()
        except Exception as exc:
            self.call_from_thread(self._run_failed, exc)
            return
        self.call_from_thread(self._set_status, "mxa-status", "○ MXA disconnected")
        self.call_from_thread(self._set_status, "shutter-status", "○ Shutter / PSU disconnected")
        self.call_from_thread(self._log, "All devices disconnected")

    @work(thread=True)
    def _quit_worker(self) -> None:
        try:
            self.devices.close_all()
        finally:
            self.call_from_thread(self.exit)

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        if self._running:
            return
        button_id = event.button.id
        if button_id == "connect-mxa":
            self._connect_mxa_worker()
        elif button_id == "connect-shutter":
            self._connect_shutter_worker()
        elif button_id == "bandwidth":
            self._last_results = []
            self._bandwidth_worker()
        elif button_id == "frequency":
            self._last_results = []
            center = float(self.query_one("#frequency-center", Input).value or "377.1052067")
            channel = int(self.query_one("#wavemeter-channel", Input).value or "1")
            self._frequency_worker(center, channel)
        elif button_id == "capture":
            self._last_results = []
            self.call_from_thread(self._start_run, 1, "Capturing traces…")
            self._capture_worker()
        elif button_id == "disconnect":
            self._disconnect_worker()

    def action_bandwidth(self) -> None:
        if not self._running:
            self._last_results = []
            self._bandwidth_worker()

    def action_frequency(self) -> None:
        if not self._running:
            self._last_results = []
            center = float(self.query_one("#frequency-center", Input).value or "377.1052067")
            channel = int(self.query_one("#wavemeter-channel", Input).value or "1")
            self._frequency_worker(center, channel)

    def action_capture(self) -> None:
        if not self._running:
            self._last_results = []
            self.call_from_thread(self._start_run, 1, "Capturing traces…")
            self._capture_worker()

    def action_disconnect(self) -> None:
        if not self._running:
            self._disconnect_worker()

    def action_quit_app(self) -> None:
        self._quit_worker()


def main() -> None:
    """Launch the iyzee terminal application."""
    IyzTuiApp().run()


if __name__ == "__main__":
    main()
