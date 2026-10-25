"""Behavioral tests for the unified Results screen."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from helpers import async_test, plain, save_run, wait_until
from test_scope_workflows import FakeScope
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, ListView, Select, Static

from iyzee.devices.scope import Channel, LeCroy
from iyzee.scope_workflows import acquire_scope_recording, save_scope_acquisition
from iyzee.tui import app as app_mod
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
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> AsyncIterator[tuple[ResultsScreen, Pilot]]:
    monkeypatch.setattr(results_mod, "_DATA_ROOT", data_root)
    app = app_mod.IyzeeApp()
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
        assert "Mean relative noise: 2.000 dB" in point_summary

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
        assert "Minimum relative noise: 2.000 dB" in plain(
            screen.query_one("#results-point-summary", Static)
        )


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
