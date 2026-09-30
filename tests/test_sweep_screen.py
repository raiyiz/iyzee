"""Tests for the Sweep page: form parsing and validation, the "is everything
connected" guards and banner, abort, Capture trace, and save-as-you-go.

Actual measurement is faked at run_sequence (experiment/'s own suite
covers the real thing); what is exercised here is everything the page adds
around it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from helpers import FakeHandle, async_test, make_result, notifications, plain, wait_until
from textual.pilot import Pilot
from textual.widgets import Button, Input, RichLog, Select, Static

from iyzee.tui import app as app_mod
from iyzee.tui.app import IyzeeApp
from iyzee.tui.screens import sweep as sweep_mod
from iyzee.tui.screens.sweep import (
    MAX_POINTS,
    FieldError,
    SweepScreen,
    _positive_float,
    _positive_int,
)

# -- standalone validators ---------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("20000", 20000.0), ("0.5", 0.5)])
def test_positive_float_accepts_valid_input(raw: str, expected: float) -> None:
    assert _positive_float(raw, "field") == expected


@pytest.mark.parametrize(
    "raw",
    ["0", "-5", "abc"],
)
def test_positive_float_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError, match="field"):
        _positive_float(raw, "field")


@pytest.mark.parametrize(
    "raw,maximum,expected",
    [
        ("19", None, 19),
        (str(MAX_POINTS), MAX_POINTS, MAX_POINTS),
    ],
)
def test_positive_int_accepts_valid_input(raw: str, maximum: int | None, expected: int) -> None:
    assert _positive_int(raw, "field", maximum=maximum) == expected


@pytest.mark.parametrize(
    "raw,maximum,match",
    [
        ("0", None, "field"),
        ("-1", None, "field"),
        ("abc", None, "field"),
        (str(MAX_POINTS + 1), MAX_POINTS, f"at most {MAX_POINTS}"),
    ],
)
def test_positive_int_rejects_invalid_input(raw: str, maximum: int | None, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _positive_int(raw, "field", maximum=maximum)


# -- the mounted form ---------------------------------------------------------------------


async def _open_sweep_screen(pilot: Pilot[Any]) -> SweepScreen:
    await pilot.press("s")
    await pilot.pause()
    return pilot.app.query_one(SweepScreen)


@async_test
async def test_form_defaults_build_the_documented_runs() -> None:
    async with IyzeeApp().run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        steps, config = screen._build_bandwidth_run()
        assert len(steps) == 19
        assert config.res_bw_hz == pytest.approx(20000.0)
        steps, _config = screen._build_frequency_run()
        assert len(steps) == 5


@pytest.mark.parametrize(
    "field_id,label,bad,kind",
    [
        ("rbw-stop", "RBW stop", "abc", "bandwidth"),
        ("rbw-steps", "Steps", "0", "bandwidth"),
        ("freq-points", "Points", "5000", "frequency"),
    ],
)
@async_test
async def test_an_invalid_field_is_named_marked_and_focused(
    field_id: str, label: str, bad: str, kind: str
) -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        app.handles["mxa"] = FakeHandle()
        app.handles["shutter"] = FakeHandle()
        screen.query_one("#sweep-type", Select).value = kind
        await pilot.pause()
        field = screen.query_one(f"#{field_id}", Input)
        field.value = bad
        await pilot.pause()

        build = screen._build_bandwidth_run if kind == "bandwidth" else screen._build_frequency_run
        with pytest.raises(FieldError) as excinfo:
            build()
        assert isinstance(excinfo.value, ValueError)
        assert excinfo.value.field_id == field_id and label in str(excinfo.value)

        screen._start_sweep()
        await pilot.pause()
        assert field.has_class("-invalid")
        assert app.focused is field
        assert any(m.startswith("Invalid sweep parameters") for m in notifications(app))
        assert not app.sweep_running

        field.value = "5"
        await pilot.pause()
        assert not field.has_class("-invalid")


# -- pre-flight guards and the "not ready" banner ------------------------------------------


@pytest.mark.parametrize(
    "kind,connected,action,expected,button",
    [
        ("bandwidth", (), "_start_sweep", "Connect the MXA", "#run-sweep"),
        ("bandwidth", (), "_start_capture", "Connect the MXA", "#capture-trace"),
        ("frequency", ("mxa",), "_start_sweep", "Connect the shutter", "#run-sweep"),
    ],
)
@async_test
async def test_starting_without_the_needed_instrument_notifies_and_launches_nothing(
    kind: str, connected: tuple[str, ...], action: str, expected: str, button: str
) -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        for key in connected:
            app.handles[key] = FakeHandle()
        screen.query_one("#sweep-type", Select).value = kind
        await pilot.pause()

        getattr(screen, action)()
        await pilot.pause()
        assert expected in notifications(app)[0]
        assert screen.query_one(button).disabled is False


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
        assert not banner.display

        del app.handles["mxa"]
        app.instruments_changed()
        await pilot.pause()
        assert banner.display and "MXA" in plain(banner)


# -- running a (fake) sweep: abort, and saving as it goes ----------------------------------


def _sweep_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_run_sequence: Any) -> IyzeeApp:
    """An app wired so pressing Run drives fake_run_sequence instead of hardware."""
    monkeypatch.setattr(sweep_mod, "create_dirs", lambda: tmp_path)
    monkeypatch.setattr(sweep_mod, "prepare_analyzer", lambda *a, **k: None)
    monkeypatch.setattr(sweep_mod, "run_sequence", fake_run_sequence)
    app = app_mod.IyzeeApp()
    app.handles["mxa"] = FakeHandle()
    return app


async def _press_run_and_wait(app: IyzeeApp) -> None:
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.click("#run-sweep")
        await wait_until(pilot, lambda: app.last_run is not None)
        await pilot.pause(0.2)


def _points_on_disk(directory: Path) -> int:
    files = list(directory.glob("*.npz"))
    assert len(files) <= 1, f"expected a single checkpoint file, found {files}"
    if not files:
        return 0
    with np.load(files[0], allow_pickle=False) as archive:
        return len(archive["x_values"])


def _result(index: int) -> Any:
    return make_result(f"pt{index}", float(index))


@async_test
async def test_abort_is_acknowledged_and_takes_effect_at_the_next_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started, release = threading.Event(), threading.Event()

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        started.set()
        release.wait(5)
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
async def test_points_are_on_disk_while_the_sweep_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    on_disk_after_each_step: list[int] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        for index in range(3):
            on_step(index, 3, object(), _result(index), None)
            on_disk_after_each_step.append(_points_on_disk(tmp_path))

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    await _press_run_and_wait(app)

    assert on_disk_after_each_step == [1, 2, 3]
    assert _points_on_disk(tmp_path) == 3
    assert app.last_run is not None and len(app.last_run.results) == 3
    assert app.last_run.path is not None and app.last_run.path.parent == tmp_path
    assert not app.sweep_running


@async_test
async def test_a_failing_save_is_reported_once_and_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps_run: list[int] = []

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        for index in range(3):
            on_step(index, 3, object(), _result(index), None)
            steps_run.append(index)

    def broken_save(*args, **kwargs):
        raise OSError("No space left on device [/data]")

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    monkeypatch.setattr(sweep_mod, "save_step_results", broken_save)
    await _press_run_and_wait(app)
    warnings = [m for m in notifications(app) if "Could not save" in m]
    assert len(warnings) == 1, warnings
    assert steps_run == [0, 1, 2]
    assert app.last_run is not None and len(app.last_run.results) == 3
    assert app.last_run.path is None


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
        await pilot.pause(0.1)
        await wait_until(pilot, lambda: not screen.query_one("#capture-trace").disabled)
        assert fake_mxa.trace_updates == [(1, True), (1, False)]
        log = " ".join(
            str(seg) for line in screen.query_one("#sweep-log", RichLog).lines for seg in line
        )
        assert "Captured 3 point(s)" in log
