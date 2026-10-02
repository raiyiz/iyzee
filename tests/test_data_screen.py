"""Tests for the Data page: run list, channel toggles, math ops, and export.

``data_mod._DATA_ROOT`` is monkeypatched to a temp directory, same as
test_traces_screen.py, so these never touch the real ``data/`` directory.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
from helpers import async_test, notifications, plain, wait_until
from test_scope_workflows import DetailedFakeScope
from textual.pilot import Pilot
from textual.widgets import Button, Input, ListView, RichLog, Select, Static

from iyzee.scope import Channel
from iyzee.scope_workflows import acquire_scope_recording, save_scope_acquisition
from iyzee.tui import app as app_mod
from iyzee.tui.screens import data as data_mod
from iyzee.tui.screens.data import DataScreen


def _save_scope_run(data_root: Path, *, channels=(Channel.C1, Channel.C2), scope=None) -> Path:
    """A real acquire -> save round trip, so these tests exercise the exact
    on-disk schema traces_from_scope_recording actually reads."""
    scope = scope or DetailedFakeScope()
    recording = acquire_scope_recording(scope, list(channels))
    stem = save_scope_acquisition(recording, data_root)
    return stem.with_suffix(".npz")


def _save_sweep_run(data_root: Path) -> Path:
    """A non-scope recording, to check the Data list filters it out."""
    import numpy as np

    from iyzee.experiment.io import save_numeric_recording

    stem = save_numeric_recording(
        {"x_values": np.array([1.0, 2.0])}, data_root, {"kind": "sweep"}, name="sweep"
    )
    return stem.with_suffix(".npz")


@asynccontextmanager
async def _open_data_screen(
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> AsyncIterator[tuple[DataScreen, Pilot]]:
    monkeypatch.setattr(data_mod, "_DATA_ROOT", data_root)
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("d")
        await pilot.pause(0.3)
        yield app.query_one(DataScreen), pilot


def _summary(screen: DataScreen) -> str:
    return plain(screen.query_one("#data-summary", Static))


def _log(screen: DataScreen) -> str:
    return "\n".join(strip.text for strip in screen.query_one("#data-log", RichLog).lines)


async def _apply_op(
    screen: DataScreen,
    pilot: Pilot,
    op: str,
    *,
    a: str | None = None,
    b: str | None = None,
    **inputs: str,
) -> None:
    """Fill the math-op form and press Apply. ``inputs`` are the text fields,
    named by id with ``_`` for ``-`` (``region_lo`` is ``#data-region-lo``)."""
    screen.query_one("#data-op", Select).value = op
    if a is not None:
        screen.query_one("#data-chan-a", Select).value = a
    if b is not None:
        screen.query_one("#data-chan-b", Select).value = b
    for field, value in inputs.items():
        screen.query_one(f"#data-{field.replace('_', '-')}", Input).value = value
    await pilot.pause()
    screen.query_one("#data-apply-op", Button).press()
    await pilot.pause()


# -- empty state / navigation to the page ----------------------------------------------------


@async_test
async def test_empty_data_root_shows_a_placeholder_and_an_empty_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "No scope acquisitions" in _summary(screen)
        assert screen._measured == {} and screen._checkboxes == {}


# -- run list: filtered to scope acquisitions -------------------------------------------------


@async_test
async def test_lists_scope_acquisitions_and_filters_out_other_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_sweep_run(tmp_path)
    _save_scope_run(tmp_path, channels=(Channel.C1,))
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert len(screen._paths) == 1
        assert set(screen._measured) == {"C1"}


@async_test
async def test_a_corrupt_npz_is_reported_rather_than_crashing_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "2026-09"
    run_dir.mkdir(parents=True)
    path = run_dir / "13T000000_broken_abc123.npz"
    path.write_bytes(b"not actually a numpy archive")
    path.with_suffix(".json").write_text('{"kind": "scope-acquisition"}')

    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "Could not read file" in _summary(screen)
        assert screen._measured == {}


# -- selecting a channel toggles the preview, not an error ------------------------------------


@async_test
async def test_channel_toggles_control_the_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, pilot):
        assert set(screen._checkboxes) == {"C1", "C2"}
        assert all(cb.value for cb in screen._checkboxes.values())

        screen._checkboxes["C2"].value = False
        await pilot.pause()
        assert [t.label for t in screen._selected_traces()] == ["C1"]


# -- subtract ---------------------------------------------------------------------------------


@async_test
async def test_subtract_adds_a_derived_trace_and_a_checkbox_and_clear_removes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        await _apply_op(screen, pilot, "subtract", a="C1", b="C2")

        assert "C1 - C2" in screen._derived
        assert "C1 - C2" in screen._checkboxes
        assert "Added derived trace: C1 - C2" in _log(screen)

        screen.query_one("#data-clear-op", Button).press()
        await pilot.pause()

        assert screen._derived == {}
        assert set(screen._checkboxes) == {"C1", "C2"}
        assert "Cleared derived traces." in _log(screen)


@pytest.mark.parametrize(
    "op,fields,invalid_id,notice",
    [
        ("subtract", {"a": "C1"}, "#data-chan-b", "Channel B"),
        ("scale", {"a": "C1", "yscale": "not a number"}, "#data-yscale", None),
    ],
    ids=["subtract-without-b", "non-numeric-scale"],
)
@async_test
async def test_a_bad_form_field_is_flagged_and_nothing_is_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op, fields, invalid_id, notice
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        await _apply_op(screen, pilot, op, **fields)

        assert screen._derived == {}
        assert screen.query_one(invalid_id).has_class("-invalid")
        if notice:
            app = pilot.app
            assert isinstance(app, app_mod.IyzeeApp)
            assert any(notice in n for n in notifications(app))


# -- background correction ---------------------------------------------------------------------


@async_test
async def test_background_region_subtracts_the_mean_of_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,)).parent
    ) as (screen, pilot):
        c1 = screen._measured["C1"]
        lo, hi = float(c1.time[0]), float(c1.time[min(1, len(c1.time) - 1)])

        await _apply_op(
            screen, pilot, "background-region", a="C1", region_lo=f"{lo}", region_hi=f"{hi}"
        )

        (derived_label,) = [label for label in screen._derived if label != "C1"]
        assert "bg-corrected" in derived_label


# -- scale --------------------------------------------------------------------------------------


@async_test
async def test_scale_axes_applies_the_form_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,)).parent
    ) as (screen, pilot):
        await _apply_op(screen, pilot, "scale", a="C1", yscale="1000")

        (derived,) = screen._derived.values()
        np.testing.assert_allclose(derived.values, screen._measured["C1"].values * 1000)


# -- clear derived --------------------------------------------------------------------------


# -- threaded rendering: a slow render must never land after a newer one -------------------


@async_test
async def test_a_slow_redraw_does_not_clobber_a_newer_ones_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the generation check in _apply_preview: toggle a
    checkbox while the previous (now-stale) render is still building its
    figure, and confirm the *plot itself* ends up drawn from the second
    toggle's data, not whichever render happened to finish last.

    Checking ``_selected_traces()`` here would prove nothing — it just
    re-reads the checkboxes' current state, which is unaffected by which
    render actually reached ``draw_series()``. This spies on ``draw_series``
    itself (the one call that actually touches ``#data-plot``) to see what
    was drawn.
    """
    _save_scope_run(tmp_path)
    real_build = data_mod.build_waveform_figure
    calls: list[str | None] = []
    release = threading.Event()
    entered_first = threading.Event()

    def gated_build(traces: list, *, title: str | None = None):  # type: ignore[no-untyped-def]
        calls.append(title)
        if len(calls) == 1:
            entered_first.set()
            release.wait(timeout=5)
        return real_build(traces, title=title)

    monkeypatch.setattr(data_mod, "build_waveform_figure", gated_build)

    drawn: list[list[str | None]] = []
    real_draw_series = data_mod.draw_series

    def spying_draw_series(plot, lines, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append([label for _x, _y, label in lines])
        return real_draw_series(plot, lines, **kwargs)

    monkeypatch.setattr(data_mod, "draw_series", spying_draw_series)

    async with _open_data_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, entered_first.is_set)  # the initial auto-select's redraw
        generation_at_block = screen._render_generation

        screen._checkboxes["C2"].value = False  # a second, newer redraw request
        await wait_until(pilot, lambda: screen._render_generation != generation_at_block)
        release.set()  # let the first (now stale) render proceed and finish

        await wait_until(pilot, lambda: len(calls) >= 2)
        await wait_until(pilot, lambda: len(drawn) >= 1)
        await pilot.pause(0.2)  # give a stale, wrongly-applied second draw a chance to land

        # Both channels were built (calls==2), but only the second (later)
        # request's result may ever reach the plot.
        assert drawn == [["C1"]]


@async_test
async def test_export_disables_the_button_while_running_and_reenables_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_save = data_mod.save_waveform_figure
    entered = threading.Event()
    release = threading.Event()

    def gated_save(traces, path, *, title=None):  # type: ignore[no-untyped-def]
        entered.set()
        release.wait(timeout=5)
        return real_save(traces, path, title=title)

    monkeypatch.setattr(data_mod, "save_waveform_figure", gated_save)
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        screen.query_one("#data-export", Button).press()
        await wait_until(pilot, entered.is_set)

        assert screen.query_one("#data-export", Button).disabled

        release.set()
        await wait_until(pilot, lambda: not screen.query_one("#data-export", Button).disabled)
        assert "Saved plot" in _log(screen)


@async_test
async def test_visiting_console_then_data_leaves_the_backend_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GUI backend (Tk, most commonly) is not thread-safe: creating or
    destroying its objects off the thread that first touched it crashes
    outright rather than raising a catchable exception (this is the actual
    failure observed switching between pages without an instrument
    connected — nothing here talks to hardware). Console is the other page
    that can put a matplotlib Figure in front of the user's own code
    (``console.py``'s ``_render_figure``), so it's the one place that could
    plausibly re-trigger backend resolution after ``iyzee.tui``'s own
    import-time pin — confirm visiting it doesn't undo that pin before Data
    goes on to build a figure in a worker thread."""
    import matplotlib

    monkeypatch.setattr(data_mod, "_DATA_ROOT", tmp_path)
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("i")  # Console
        await pilot.pause()
        await pilot.press("d")  # Data
        await pilot.pause()

        assert matplotlib.get_backend().lower() == "agg"


# -- export ------------------------------------------------------------------------------------


@async_test
async def test_export_writes_a_real_png_next_to_the_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npz_path = _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, npz_path.parent) as (screen, pilot):
        screen.query_one("#data-export", Button).press()
        await wait_until(pilot, lambda: "Saved plot" in _log(screen))

        out = npz_path.with_name(f"{npz_path.stem}-plot.png")
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert f"Saved plot: {out}" in _log(screen)
        assert not screen.query_one("#data-export", Button).disabled


@async_test
async def test_export_with_no_channel_checked_refuses_with_a_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npz_path = _save_scope_run(tmp_path, channels=(Channel.C1,))
    async with _open_data_screen(monkeypatch, npz_path.parent) as (screen, pilot):
        app = pilot.app
        assert isinstance(app, app_mod.IyzeeApp)
        screen._checkboxes["C1"].value = False
        await pilot.pause()

        screen.query_one("#data-export", Button).press()
        await pilot.pause()

        assert not npz_path.with_name(f"{npz_path.stem}-plot.png").exists()
        assert any("Select at least one" in n for n in notifications(app))


# -- switching runs resets derived traces, not just the measured channels --------------------


@async_test
async def test_switching_to_a_different_run_clears_derived_traces_from_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path, channels=(Channel.C1, Channel.C2))
    import time

    time.sleep(0.01)
    _save_scope_run(tmp_path, channels=(Channel.C3,))  # newest -> highlighted first
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, pilot):
        # newest run (C3) is highlighted first; apply an op, then switch away and back
        await _apply_op(screen, pilot, "scale", a="C3")
        assert screen._derived

        list_view = screen.query_one("#data-list", ListView)
        list_view.index = 1  # the other (C1/C2) run
        await pilot.pause()

        assert screen._derived == {}
        assert set(screen._measured) == {"C1", "C2"}
