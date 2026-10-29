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

from iyzee.tui import app as app_mod
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens import sweep as sweep_mod
from iyzee.experiment import runner as runner_mod
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


@async_test
async def test_the_form_defaults_validation_and_missing_instruments() -> None:
    # -- form defaults build the documented runs and the duration applies to both
    async def _form_defaults_build_the_documented_runs_():
        async with IyzeeApp().run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            bandwidth = screen._build_bandwidth_run()
            assert len(bandwidth.steps) == 19
            assert bandwidth.config.res_bw_hz == pytest.approx(20000.0)
            frequency = screen._build_frequency_run()
            assert len(frequency.steps) == 5
            # The defaults the issue asked for: 10 ms sweeps, 10 MHz between laser points.
            assert bandwidth.config.sweep_duration_ms == frequency.config.sweep_duration_ms == 10
            gaps = [b.x_value - a.x_value for a, b in itertools.pairwise(frequency.steps)]
            assert all(gap == pytest.approx(10e-6) for gap in gaps)  # 10 MHz, in THz

            screen.query_one("#sweep-duration", Input).value = "25"
            await pilot.pause()
            assert screen._build_bandwidth_run().config.sweep_duration_ms == 25
            assert screen._build_frequency_run().config.sweep_duration_ms == 25

    await _form_defaults_build_the_documented_runs_()

    # -- an invalid field is named marked and focused
    async def _an_invalid_field_is_named_marked_and_foc():
        app = IyzeeApp()
        async with app.run_test() as pilot:
            screen = await _open_sweep_screen(pilot)
            for key in ("mxa", "shutter", "wavemeter"):
                app.handles[key] = FakeHandle()
            for field_id, label, bad, kind in (
                ("rbw-stop", "RBW stop", "abc", "bandwidth"),
                ("rbw-steps", "Steps", "0", "bandwidth"),
                ("freq-points", "Points", "5000", "frequency"),
                ("sweep-duration", "Sweep duration", "abc", "frequency"),
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

                field.value = good  # editing clears the marker
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

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
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

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
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
