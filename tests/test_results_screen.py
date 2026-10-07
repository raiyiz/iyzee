"""Behavioral tests for the unified Results screen."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from helpers import async_test, notifications, plain, save_run, wait_until
from test_scope_workflows import FakeScope
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, ListView, Select, Static

from iyzee.devices.scope import Channel, LeCroy
from iyzee.scope_workflows import acquire_scope_recording, save_scope_acquisition
from iyzee.tui import app as app_mod
from iyzee.tui import plotting as plotting_mod
from iyzee.tui.screens import results as results_mod
from iyzee.tui.screens.results import ResultsScreen
from iyzee.waveform_math import scale_trace, subtract_background, subtract_traces


def _save_scope_run(
    data_root: Path,
    *,
    channels: tuple[Channel, ...] = (Channel.C1, Channel.C2),
) -> Path:
    recording = acquire_scope_recording(cast(LeCroy, FakeScope()), list(channels))
    return save_scope_acquisition(recording, data_root).with_suffix(".npz")


@asynccontextmanager
async def _open_results_screen(
    monkeypatch: pytest.MonkeyPatch, data_root: Path, prefs_file: Path | None = None
) -> AsyncIterator[tuple[ResultsScreen, Pilot]]:
    monkeypatch.setattr(results_mod, "_DATA_ROOT", data_root)
    app = app_mod.IyzeeApp(prefs_file=prefs_file)
    async with app.run_test() as pilot:
        await pilot.press("t")
        screen = app.query_one(ResultsScreen)
        await wait_until(pilot, lambda: screen.query_one("#results-list", ListView).has_focus)
        yield screen, pilot


def _summary(screen: ResultsScreen) -> str:
    return plain(screen.query_one("#results-summary", Static))


async def _apply_op(
    screen: ResultsScreen,
    pilot: Pilot,
    operation: str,
    *,
    a: str | None = None,
    b: str | None = None,
    **inputs: str,
) -> None:
    screen.query_one("#results-op", Select).value = operation
    if a is not None:
        screen.query_one("#results-chan-a", Select).value = a
    if b is not None:
        screen.query_one("#results-chan-b", Select).value = b
    for field, value in inputs.items():
        screen.query_one(f"#results-{field.replace('_', '-')}", Input).value = value
    await pilot.pause()
    screen.query_one("#results-apply-op", Button).press()
    await pilot.pause()


@async_test
async def test_empty_results_folder_explains_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "No recordings yet" in _summary(screen)
        assert str(tmp_path) in plain(screen.query_one("#results-hint", Static))


@async_test
async def test_one_browser_switches_between_sweep_and_scope_recordings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = save_run(tmp_path, "2026-10", mtime=1000)
    scope = _save_scope_run(tmp_path)
    os.utime(scope, (2000, 2000))

    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        assert len(screen._paths) == 2
        assert screen._paths[0] == scope
        await wait_until(pilot, lambda: set(screen._measured) == {"C1", "C2"})
        assert not screen.query_one("#results-points").display
        assert screen.query_one("#results-op-panel").display

        listing = screen.query_one("#results-list", ListView)
        assert listing.index == 0
        listing.focus()
        await pilot.press("down")
        await wait_until(pilot, lambda: "1 point(s)" in _summary(screen))
        assert listing.index == 1
        assert screen._recording is not None
        assert screen._recording.path == sweep
        assert screen.query_one("#results-points").display
        assert not screen.query_one("#results-op-panel").display


@async_test
async def test_corrupt_recording_is_reported_without_crashing_the_results_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "2026-10"
    run_dir.mkdir()
    path = run_dir / "13T000000_broken_abc123.npz"
    path.write_bytes(b"not a numpy archive")
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: "Could not read file" in _summary(screen))
        assert screen._recording is None
        assert screen._measured == {}


@async_test
async def test_frequency_sweep_keeps_requested_and_measured_frequency_navigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iyzee.experiment.core import StepResult

    save_run(
        tmp_path,
        "frequency",
        StepResult(
            label="pt0",
            x_value=377.100000,
            x_unit="THz",
            traces={"squeezing": [1.0, 3.0], "shot_noise": [0.0, 0.0]},
            meta={"wavemeter_channel": 4, "measured_frequency_thz": 377.100001},
        ),
        StepResult(
            label="pt1",
            x_value=377.100010,
            x_unit="THz",
            traces={"squeezing": [2.0, 4.0], "shot_noise": [0.0, 0.0]},
            meta={"wavemeter_channel": 4, "measured_frequency_thz": 377.100011},
        ),
    )

    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: "2 point(s)" in _summary(screen))
        table = screen.query_one("#results-points", DataTable)
        assert len(table.rows) == 2
        point_summary = plain(screen.query_one("#results-point-summary", Static))
        assert "Requested: 377.100000000 THz" in point_summary
        assert "Measured: 377.100001000 THz" in point_summary
        assert "Mean delta: 2.000" in point_summary

        table.move_cursor(row=1, column=0)
        await wait_until(
            pilot,
            lambda: (
                "Measured: 377.100011000 THz"
                in plain(screen.query_one("#results-point-summary", Static))
            ),
        )

        screen.query_one("#results-statistic", Select).value = "minimum"
        await pilot.pause()
        assert "Minimum delta: 2.000" in plain(screen.query_one("#results-point-summary", Static))


@async_test
async def test_scope_channel_selection_changes_the_actual_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    drawn: list[list[str | None]] = []
    real_draw = results_mod.draw_series

    def spy(plot, series, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append([label for _x, _y, label in series])
        return real_draw(plot, series, **kwargs)

    monkeypatch.setattr(results_mod, "draw_series", spy)

    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: drawn and drawn[-1] == ["C1", "C2"])
        screen._checkboxes["C2"].value = False
        await wait_until(pilot, lambda: drawn and drawn[-1] == ["C1"])


@async_test
async def test_scope_operations_apply_and_chain_through_derived_traces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: set(screen._measured) == {"C1", "C2"})
        a = screen._measured["C1"]
        b = screen._measured["C2"]
        known: set[str] = set()

        def expect_one_new(label: str) -> None:
            """Each operation adds exactly one derived trace and disturbs no other."""
            known.add(label)
            assert set(screen._derived) == known

        await _apply_op(screen, pilot, "subtract", a="C1", b="C2")
        subtracted = subtract_traces(a, b)
        expect_one_new(subtracted.label)
        np.testing.assert_allclose(screen._derived[subtracted.label].values, subtracted.values)

        lo = float(a.time[0])
        hi = float(a.time[min(1, len(a.time) - 1)])
        await _apply_op(
            screen,
            pilot,
            "background-region",
            a="C1",
            region_lo=str(lo),
            region_hi=str(hi),
        )
        expected = subtract_background(a, region=(lo, hi))
        expect_one_new(expected.label)
        np.testing.assert_allclose(screen._derived[expected.label].values, expected.values)

        await _apply_op(screen, pilot, "background-reference", a="C1", b="C2")
        expected = subtract_background(a, reference=b)
        expect_one_new(expected.label)
        np.testing.assert_allclose(screen._derived[expected.label].values, expected.values)

        await _apply_op(
            screen,
            pilot,
            "scale",
            a="C1",
            xscale="2",
            xoffset="1",
            yscale="3",
            yoffset="4",
        )
        expected = scale_trace(a, x_scale=2.0, x_offset=1.0, y_scale=3.0, y_offset=4.0)
        expect_one_new(expected.label)
        np.testing.assert_allclose(screen._derived[expected.label].time, expected.time)
        np.testing.assert_allclose(screen._derived[expected.label].values, expected.values)

        await _apply_op(
            screen,
            pilot,
            "scale",
            a=subtracted.label,
            xscale="1",
            xoffset="0",
            yscale="1000",
            yoffset="0",
        )
        chained = scale_trace(screen._derived[subtracted.label], y_scale=1000.0)
        expect_one_new(chained.label)
        np.testing.assert_allclose(screen._derived[chained.label].values, chained.values)


@async_test
async def test_scope_operation_form_shows_only_relevant_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: set(screen._measured) == {"C1", "C2"})
        screen.query_one("#results-op", Select).value = "background-region"
        await pilot.pause()

        assert screen.query_one("#results-field-chan-a").display
        assert screen.query_one("#results-field-region-lo").display
        assert screen.query_one("#results-field-region-hi").display
        assert not screen.query_one("#results-field-chan-b").display
        assert not screen.query_one("#results-field-yscale").display


@pytest.mark.parametrize("invalid", ["not-a-number", "nan", "inf"])
@async_test
async def test_invalid_scope_input_creates_no_derived_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    _save_scope_run(tmp_path, channels=(Channel.C1,))
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: set(screen._measured) == {"C1"})
        await _apply_op(screen, pilot, "scale", a="C1", yscale=invalid)
        assert screen._derived == {}
        assert screen.query_one("#results-yscale").has_class("-invalid")
        assert any(
            "Y scale" in message for message in notifications(cast(app_mod.IyzeeApp, pilot.app))
        )


@async_test
async def test_slow_scope_preview_cannot_overwrite_a_newer_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    real_prepare = plotting_mod.prepare_series
    call_count = 0

    def gated_prepare(x, y, label, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            entered.set()
            release.wait(timeout=5)
        return real_prepare(x, y, label, *args, **kwargs)

    monkeypatch.setattr(plotting_mod, "prepare_series", gated_prepare)
    drawn: list[list[str | None]] = []
    real_draw = results_mod.draw_series

    def spy(plot, series, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append([label for _x, _y, label in series])
        return real_draw(plot, series, **kwargs)

    monkeypatch.setattr(results_mod, "draw_series", spy)

    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, entered.is_set)
        generation = screen._render_generation
        screen._checkboxes["C2"].value = False
        await wait_until(pilot, lambda: screen._render_generation != generation)
        release.set()
        await wait_until(pilot, lambda: drawn and drawn[-1] == ["C1"])
        await pilot.pause(0.2)
        assert drawn == [["C1"]]


@async_test
async def test_export_writes_a_real_png_and_runs_off_the_ui_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npz_path = _save_scope_run(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    real_save = results_mod.save_waveform_figure

    def gated_save(traces, path, *, title=None):  # type: ignore[no-untyped-def]
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        release.wait(timeout=5)
        return real_save(traces, path, title=title)

    monkeypatch.setattr(results_mod, "save_waveform_figure", gated_save)
    async with _open_results_screen(monkeypatch, tmp_path) as (screen, pilot):
        screen.query_one("#results-export", Button).press()
        await wait_until(pilot, entered.is_set)
        assert screen.query_one("#results-export").disabled
        release.set()
        await wait_until(pilot, lambda: not screen.query_one("#results-export").disabled)
        out = npz_path.with_name(f"{npz_path.stem}-plot.png")
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@async_test
async def test_list_width_keys_resize_clamp_and_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_file = tmp_path / "config" / "tui.json"
    async with _open_results_screen(monkeypatch, tmp_path, prefs_file) as (screen, pilot):
        run_list = screen.query_one("#results-list")
        assert run_list.has_class(f"w-{results_mod.DEFAULT_LIST_WIDTH}")

        await pilot.press("]")
        assert run_list.has_class("w-40") and not run_list.has_class("w-35")
        for _ in range(20):  # far past the end: clamped, not an error
            await pilot.press("[")
        assert run_list.has_class("w-20")

    assert json.loads(prefs_file.read_text())["results_list_width"] == 20
    async with _open_results_screen(monkeypatch, tmp_path, prefs_file) as (screen, _pilot):
        assert screen.query_one("#results-list").has_class("w-20")  # remembered


@async_test
async def test_unusable_saved_list_width_falls_back_to_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefs_file = tmp_path / "tui.json"
    prefs_file.write_text('{"results_list_width": 7}')
    async with _open_results_screen(monkeypatch, tmp_path, prefs_file) as (screen, _pilot):
        assert screen.query_one("#results-list").has_class(f"w-{results_mod.DEFAULT_LIST_WIDTH}")
