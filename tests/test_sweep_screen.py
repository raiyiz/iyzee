"""Tests for the Sweep page: form parsing and validation, the "is everything
connected" guards and banner, abort, Capture trace, and save-as-you-go.

Actual measurement is faked at run_sequence (experiment/'s own suite
covers the real thing); what is exercised here is everything the page adds
around it.
"""

from __future__ import annotations

import asyncio
import itertools
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


def test_the_standalone_validators_accept_good_input_and_name_the_field_on_bad() -> None:
    assert _positive_float("20000", "field") == 20000.0 and _positive_float("0.5", "field") == 0.5
    for raw in ("0", "-5", "abc"):
        with pytest.raises(ValueError, match="field"):
            _positive_float(raw, "field")

    assert _positive_int("19", "field", maximum=None) == 19
    assert _positive_int(str(MAX_POINTS), "field", maximum=MAX_POINTS) == MAX_POINTS
    for raw in ("0", "-1", "abc"):
        with pytest.raises(ValueError, match="field"):
            _positive_int(raw, "field", maximum=None)
    with pytest.raises(ValueError, match=f"at most {MAX_POINTS}"):
        _positive_int(str(MAX_POINTS + 1), "field", maximum=MAX_POINTS)


# -- the mounted form ---------------------------------------------------------------------


async def _open_sweep_screen(pilot: Pilot[Any]) -> SweepScreen:
    await pilot.press("s")
    await pilot.pause()
    return pilot.app.query_one(SweepScreen)


@async_test
async def test_form_defaults_build_the_documented_runs_and_the_duration_applies_to_both() -> None:
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


@async_test
async def test_an_invalid_field_is_named_marked_and_focused() -> None:
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
                screen._build_bandwidth_run if kind == "bandwidth" else screen._build_frequency_run
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


# -- pre-flight guards and the "not ready" banner ------------------------------------------


@async_test
async def test_starting_without_the_needed_instrument_notifies_and_launches_nothing() -> None:
    app = IyzeeApp()
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        for kind, connected, action, expected, button in (
            ("bandwidth", (), "_start_sweep", "Connect the MXA", "#run-sweep"),
            ("bandwidth", (), "_start_capture", "Connect the MXA", "#capture-trace"),
            ("frequency", ("mxa",), "_start_sweep", "Connect the shutter", "#run-sweep"),
            (
                "frequency",
                ("mxa", "shutter"),
                "_start_sweep",
                "Connect the wavemeter",
                "#run-sweep",
            ),
        ):
            app.handles.clear()
            for key in connected:
                app.handles[key] = FakeHandle()
            screen.query_one("#sweep-type", Select).value = kind
            await pilot.pause()

            getattr(screen, action)()
            await pilot.pause()
            assert expected in notifications(app)[-1]
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


def _points_on_disk(directory: Path) -> int:
    files = list(directory.glob("*.npz"))
    assert len(files) <= 1, f"expected a single checkpoint file, found {files}"
    if not files:
        return 0
    with np.load(files[0], allow_pickle=False) as archive:
        return len(archive["x_values"])


def _result(index: int) -> Any:
    return make_result(f"pt{index}", float(index))


def _locked_by_someone_else(lock: Any) -> bool:
    """Whether ``lock`` is held by another thread (an RLock is re-entrant for its owner)."""
    got: list[bool] = []
    thread = threading.Thread(target=lambda: got.append(lock.acquire(blocking=False)))
    thread.start()
    thread.join()
    if got[0]:
        lock.release()
    return not got[0]


@pytest.mark.parametrize(
    "kind,needed",
    [("bandwidth", {"mxa"}), ("frequency", {"mxa", "shutter", "wavemeter"})],
)
@async_test
async def test_a_run_gets_the_connected_devices_and_holds_every_lock_it_needs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, needed: set[str]
) -> None:
    devices = {key: object() for key in ("mxa", "shutter", "wavemeter")}
    seen: dict[str, Any] = {}

    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        seen["ctx"] = ctx
        seen["held"] = {k for k in devices if _locked_by_someone_else(app.handles[k].lock)}

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    for key, device in devices.items():
        app.handles[key] = FakeHandle(device=device)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        app.query_one("#sweep-type", Select).value = kind
        await pilot.pause()
        await pilot.click("#run-sweep")
        # No points are recorded, so there is no last_run: wait for the run itself to end.
        await wait_until(pilot, lambda: "held" in seen and not app.sweep_running)

    ctx = seen["ctx"]
    assert ctx.mx is devices["mxa"]
    assert ctx.shutter is (devices["shutter"] if "shutter" in needed else None)
    assert ctx.wavemeter is (devices["wavemeter"] if "wavemeter" in needed else None)
    assert seen["held"] == needed  # no more, no fewer


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

    def get_errors(self) -> list[str]:
        return []


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
    assert meta["config"]["applied_settings"] == {}
    assert [(f["index"], f["error_type"]) for f in meta["failed_steps"]] == [(1, "TimeoutError")]


@async_test
async def test_an_aborted_sweep_is_marked_aborted_in_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        screen = app.query_one(sweep_mod.SweepScreen)
        screen._abort_event.set()
        on_step(0, 3, object(), _result(0), None)  # raises SweepAborted after the point is kept

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    await _press_run_and_wait(app)

    assert _saved_run_metadata(tmp_path)["status"] == "aborted"
    assert _points_on_disk(tmp_path) == 1


@async_test
async def test_an_unexpected_error_marks_the_run_failed_but_keeps_the_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run_sequence(steps, ctx, *, on_error, on_step):
        on_step(0, 3, object(), _result(0), None)
        raise RuntimeError("scheduler exploded")

    app = _sweep_app(monkeypatch, tmp_path, fake_run_sequence)
    await _press_run_and_wait(app)

    assert _saved_run_metadata(tmp_path)["status"] == "failed"
    assert _points_on_disk(tmp_path) == 1


# -- a worker that dies with something that is not an Exception ----------------------------------
#
# Textual handles Exception (and cancellation) from a worker thread, nothing else: a
# KeyboardInterrupt or SystemExit escaping one ends the whole app with no message, and a
# swallowed CancelledError leaves the page "running" for good.


def _log_text(screen: SweepScreen) -> str:
    return " ".join(
        str(seg) for line in screen.query_one("#sweep-log", RichLog).lines for seg in line
    )


@pytest.mark.parametrize("exc_type", [asyncio.CancelledError])
@async_test
async def test_a_sweep_worker_dying_with_a_base_exception_does_not_take_the_app_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc_type: type[BaseException]
) -> None:
    def dying_run_sequence(steps, ctx, *, on_error, on_step):
        for index in range(2):
            on_step(index, 5, steps[index], _result(index), None)
        raise exc_type()

    app = _sweep_app(monkeypatch, tmp_path, dying_run_sequence)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.query_one(SweepScreen)
        await pilot.click("#run-sweep")
        await wait_until(pilot, lambda: app.last_run is not None)

        assert app.is_running, "the app must survive its worker"
        assert app.sweep_running is False, "the page must not be left stuck in 'running'"
        assert not screen.query_one("#run-sweep", Button).disabled
        assert exc_type.__name__ in " ".join(notifications(app))
        assert "stopped unexpectedly" in _log_text(screen)
        # What was measured before it died is kept, and the file says the run failed.
        last_run = app.last_run
        assert last_run is not None and last_run.path is not None
        assert len(last_run.results) == 2
        assert _points_on_disk(tmp_path) == 2
        assert '"failed"' in last_run.path.with_suffix(".json").read_text()


@pytest.mark.parametrize("exc_type", [SystemExit])
@async_test
async def test_a_capture_worker_dying_with_a_base_exception_does_not_take_the_app_with_it(
    monkeypatch: pytest.MonkeyPatch, exc_type: type[BaseException]
) -> None:
    def dying_acquire(mx, trace_num):
        raise exc_type()

    monkeypatch.setattr(sweep_mod, "acquire_trace", dying_acquire)
    app = IyzeeApp()
    app.handles["mxa"] = FakeHandle(device=object())
    async with app.run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        screen._start_capture()
        await wait_until(pilot, lambda: not screen.query_one("#capture-trace").disabled)

        assert app.is_running
        assert app.sweep_running is False
        assert "Capture failed" in _log_text(screen)


@async_test
async def test_even_frequency_sweeps_are_centered_symmetrically() -> None:
    async with IyzeeApp().run_test() as pilot:
        screen = await _open_sweep_screen(pilot)
        screen.query_one("#freq-points", Input).value = "4"
        await pilot.pause()

        sweep = screen._build_frequency_run()
        offsets = [step.x_value - 377.1052067 for step in sweep.steps]

        np.testing.assert_allclose(
            offsets,
            np.array([-1.5, -0.5, 0.5, 1.5]) * 10e-6,
            atol=1e-12,
        )
        assert sum(offsets) == pytest.approx(0.0, abs=1e-12)
