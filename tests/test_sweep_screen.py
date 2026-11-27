"""Tests for the Sweep page: form parsing and validation, the "is everything
connected" guards and banner, abort, Capture trace, and save-as-you-go.

Actual measurement is faked at run_sequence (experiment/'s own suite
covers the real thing); what is exercised here is everything the page adds
around it.
"""

from __future__ import annotations

import itertools
import threading
from pathlib import Path
from typing import Any

import pytest
from helpers import FakeHandle, async_test, make_result, notifications, plain, wait_until
from textual.pilot import Pilot
from textual.widgets import Button, Input, RichLog, Select, Static

from iyzee.devices.wavemeter import Rb_transitions
from iyzee.experiment import FrequencyStep, runner as runner_mod
from iyzee.tui import app as app_mod
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens.sweep import (
    FieldError,
    SweepScreen,
)

# -- standalone validators ---------------------------------------------------------------


# -- the mounted form ---------------------------------------------------------------------


async def _open_sweep_screen(pilot: Pilot[Any]) -> SweepScreen:
    await pilot.press("s")
    await pilot.pause()
    return pilot.app.query_one(SweepScreen)


async def _click_visible(pilot: Pilot[Any], selector: str) -> None:
    """Scroll an off-screen control into view before exercising its click handler."""
    widget = pilot.app.query_one(selector)
    widget.scroll_visible(animate=False)
    await pilot.pause()
    await pilot.pause()
    await pilot.click(selector)


@async_test
async def test_the_form_defaults_validation_and_missing_instruments() -> None:
    async with IyzeeApp().run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        bandwidth = screen._build_bandwidth_run()
        assert len(bandwidth.steps) == 19
        assert bandwidth.config.res_bw_hz == pytest.approx(20000.0)
        assert bandwidth.config.avg_count == 200
        assert (bandwidth.config.center_hz, bandwidth.config.span_hz) == (1e6, 0)
        assert bandwidth.config.sweep_duration_ms == 10

        frequency = screen._build_frequency_run()
        assert len(frequency.steps) == 5
        assert frequency.config.sweep_duration_ms == 10
        frequency_steps = [step for step in frequency.steps if isinstance(step, FrequencyStep)]
        assert len(frequency_steps) == len(frequency.steps)
        assert all(step.relax_time_s == pytest.approx(0.5) for step in frequency_steps)
        gaps = [b.x_value - a.x_value for a, b in itertools.pairwise(frequency_steps)]
        assert all(gap == pytest.approx(10e-6) for gap in gaps)

        screen.query_one("#mxa-sweep-time", Input).value = "25"
        await pilot.pause()
        assert screen._build_bandwidth_run().config.sweep_duration_ms == 25

        screen.query_one("#freq-settle-time", Input).value = "125"
        await pilot.pause()
        settled = screen._build_frequency_run()
        settled_steps = [step for step in settled.steps if isinstance(step, FrequencyStep)]
        assert len(settled_steps) == len(settled.steps)
        assert all(step.relax_time_s == pytest.approx(0.125) for step in settled_steps)

        label, frequency_thz = next(
            (label, value) for label, value in Rb_transitions if label == "D1 - Rb87_F22"
        )
        screen.query_one("#freq-reference", Select).value = label
        await pilot.pause()
        assert not screen.query_one("#freq-center-field").display
        selected = screen._build_frequency_run()
        expected = [frequency_thz + (i - 2) * 10e-6 for i in range(5)]
        selected_steps = [step for step in selected.steps if isinstance(step, FrequencyStep)]
        assert len(selected_steps) == len(selected.steps)
        assert [step.frequency_thz for step in selected_steps] == pytest.approx(expected)

    async def _an_invalid_field_is_named_marked_and_foc():
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            for key in ("mxa", "shutter", "wavemeter"):
                app.handles[key] = FakeHandle()
            for field_id, label, bad, kind in (
                ("rbw-stop", "RBW stop", "abc", "bandwidth"),
                ("rbw-steps", "Steps", "0", "bandwidth"),
                ("mxa-span", "Span", "-1", "bandwidth"),
                ("freq-points", "Points", "5000", "frequency"),
                ("freq-settle-time", "Laser settling time", "abc", "frequency"),
            ):
                screen.query_one("#sweep-type", Select).value = kind
                await pilot.pause()
                field = screen.query_one(f"#{field_id}", Input)
                good = field.value
                field.value = bad
                await pilot.pause()

                build = (
                    screen._build_bandwidth_run
                    if kind == "bandwidth"
                    else screen._build_frequency_run
                )
                with pytest.raises(FieldError) as excinfo:
                    build()
                assert excinfo.value.field_id == field_id and label in str(excinfo.value)

                screen._start_sweep()
                await pilot.pause()
                assert field.has_class("-invalid") and app.focused is field
                assert any(m.startswith("Invalid sweep parameters") for m in notifications(app))
                assert not app.sweep_running

                field.value = good
                await pilot.pause()
                assert not field.has_class("-invalid")

    await _an_invalid_field_is_named_marked_and_foc()


# -- pre-flight guards and the "not ready" banner ------------------------------------------


@async_test
async def test_sweep_banner_tracks_the_connections_it_needs() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        banner = screen.query_one("#sweep-status", Static)
        assert banner.display
        assert "MXA" in plain(banner) and "F1" in plain(banner)

        app.handles["mxa"] = FakeHandle()
        app.instruments_changed()
        await pilot.pause()
        assert not banner.display

        screen.query_one("#sweep-type", Select).value = "frequency"
        await pilot.pause()
        assert banner.display
        assert "shutter" in plain(banner) and "MXA" not in plain(banner)

        app.handles["shutter"] = FakeHandle()
        app.instruments_changed()
        await pilot.pause()
        assert banner.display
        assert "wavemeter" in plain(banner)

        app.handles["wavemeter"] = FakeHandle()
        app.instruments_changed()
        await pilot.pause()
        assert not banner.display

        del app.handles["mxa"]
        app.instruments_changed()
        await pilot.pause()
        assert banner.display and "MXA" in plain(banner)


# -- running a (fake) sweep: abort, and saving as it goes ----------------------------------


def _sweep_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_run_sequence: Any) -> IyzeeApp:
    """An app wired so pressing Run drives fake_run_sequence instead of hardware."""
    monkeypatch.setattr(runner_mod, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(runner_mod, "prepare_analyzer", lambda *a, **k: None)
    monkeypatch.setattr(runner_mod, "run_sequence", fake_run_sequence)
    app = app_mod.IyzeeApp()
    app.handles["mxa"] = FakeHandle()
    return app


async def _press_run_and_wait(app: IyzeeApp) -> None:
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.click("#run-sweep")
        await wait_until(pilot, lambda: app.last_run is not None)


def _result(index: int) -> Any:
    return make_result(f"pt{index}", float(index))


@async_test
async def test_abort_is_acknowledged_and_takes_effect_at_the_next_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started, release = threading.Event(), threading.Event()

    def fake_run_sequence(steps, ctx, *, on_error, on_step, **_):
        started.set()
        release.wait(2)
        on_step(0, 3, object(), _result(0), None)

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.click("#run-sweep")
        await wait_until(pilot, started.is_set)
        assert app.sweep_running

        abort = app.query_one("#abort-sweep", Button)
        await pilot.click("#abort-sweep")
        await pilot.pause()
        assert str(abort.label) == "Aborting…" and abort.disabled
        log = app.query_one("#sweep-log", RichLog)
        assert "Abort requested" in " ".join(str(seg) for line in log.lines for seg in line)

        release.set()
        await wait_until(pilot, lambda: app.last_run is not None)
        await wait_until(pilot, lambda: not app.sweep_running)
        assert str(abort.label) == "Abort"
        assert app.last_run is not None and len(app.last_run.results) == 1


@async_test
async def test_reading_mxa_settings_is_safe_when_the_analyzer_is_not_connected() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        await _open_sweep_screen(pilot)
        await _click_visible(pilot, "#read-mxa-settings")
        await pilot.pause()
        assert not app.sweep_running
        assert any("Connect the MXA first" in message for message in notifications(app))


class _FakeMxaSettings:
    def get_center_freq(self) -> float:
        return 3.5e6

    def get_span(self) -> float:
        return 2.0e6

    def get_average_count(self) -> int:
        return 73

    def get_sweep_duration_ms(self) -> int:
        return 125

    def get_average_type(self) -> str:
        return "RMS"

    def get_trigger_source(self) -> str:
        return "EXT"


@async_test
async def test_mxa_settings_can_be_read_into_the_bandwidth_form() -> None:
    app = IyzeeApp()
    app.handles["mxa"] = FakeHandle(device=_FakeMxaSettings())
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        await _click_visible(pilot, "#read-mxa-settings")
        await wait_until(pilot, lambda: screen.query_one("#mxa-center", Input).value == "3.5")
        assert screen.query_one("#mxa-span", Input).value == "2"
        assert screen.query_one("#mxa-averages", Input).value == "73"
        assert screen.query_one("#mxa-sweep-time", Input).value == "125"
        assert screen.query_one("#mxa-average-type", Select).value == "RMS"
        assert screen.query_one("#mxa-trigger-source", Select).value == "EXT"


# -- "Capture trace" ---------------------------------------------------------------------


class _FakeMxaForCapture:
    """A fake MXA supporting exactly what acquire_trace()/get_frequency_axis()
    call — enough to drive a real _capture() worker end-to-end without a
    live instrument."""

    def __init__(self, power: list[float], freq: list[float]) -> None:
        self._power = power
        self._freq = freq
        self.trace_updates: list[tuple[int, bool]] = []

    def set_trace_update(self, trace_num: int, state: bool) -> None:
        self.trace_updates.append((trace_num, state))

    def single_sweep_wait(self) -> bool:
        return True

    def get_trace_data(self, trace_num: int = 1, binary: bool = True) -> list[float]:
        return self._power

    def get_frequency_axis(self) -> list[float]:
        return self._freq


@async_test
async def test_capture_trace_plots_power_vs_frequency() -> None:
    app = IyzeeApp()
    fake_mxa = _FakeMxaForCapture(power=[-70.0, -68.0, -71.0], freq=[1e6, 1.1e6, 1.2e6])
    app.handles["mxa"] = FakeHandle(device=fake_mxa)
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        screen._start_capture()
        await wait_until(pilot, lambda: not screen.query_one("#capture-trace").disabled)
        assert fake_mxa.trace_updates == [(1, True), (1, False)]
        log = " ".join(
            str(seg) for line in screen.query_one("#sweep-log", RichLog).lines for seg in line
        )
        assert "Captured 3 point(s)" in log


# -- the saved file says how the run ended ------------------------------------------------


def _saved_run_metadata(directory: Path) -> dict[str, Any]:
    import json

    (sidecar,) = directory.glob("*.json")
    return json.loads(sidecar.read_text())["run_metadata"]


@async_test
async def test_a_finished_sweep_records_its_run_id_config_and_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Saved sweeps used to carry run_metadata=null: no run id, no analyzer settings."""
    seen: list[str] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step, **_):
        seen.append(ctx.run_id)
        on_step(0, 2, object(), _result(0), None)
        # while it is running the file must already say so
        seen.append(_saved_run_metadata(tmp_path)["status"])
        on_step(1, 2, object(), None, TimeoutError("lock lagged"))

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    await _press_run_and_wait(app)

    meta = _saved_run_metadata(tmp_path)
    assert seen[1] == "running"
    assert meta["run_id"] == seen[0]
    assert meta["status"] == "completed_with_errors"
    assert meta["finished_at_utc"] and meta["started_at_utc"]
    assert meta["config"]["res_bw_hz"] > 0  # AnalyzerConfig, as the sweep used it
    assert [(f["index"], f["error_type"]) for f in meta["failed_steps"]] == [(1, "TimeoutError")]


# -- a worker that dies with something that is not an Exception ----------------------------------
#
# Textual handles Exception (and cancellation) from a worker thread, nothing else: a
# KeyboardInterrupt or SystemExit escaping one ends the whole app with no message, and a
# swallowed CancelledError leaves the page "running" for good.
