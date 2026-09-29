"""Tests for the Data page: run list, channel toggles, math ops, and export.

``data_mod._DATA_ROOT`` is monkeypatched to a temp directory, same as
test_traces_screen.py, so these never touch the real ``data/`` directory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from helpers import async_test, notifications, plain
from test_scope_workflows import DetailedFakeScope
from textual.pilot import Pilot
from textual.widgets import Button, Input, RichLog, Select, Static

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


# -- empty state / navigation to the page ----------------------------------------------------


@async_test
async def test_binding_d_opens_the_data_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert screen.is_mounted


@async_test
async def test_empty_data_root_shows_a_placeholder_and_an_empty_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "No scope acquisitions" in _summary(screen)
        assert screen._measured == {} and screen._checkboxes == {}


# -- run list: filtered to scope acquisitions -------------------------------------------------


@async_test
async def test_lists_a_saved_scope_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert len(screen._paths) == 1
        assert set(screen._measured) == {"C1", "C2"}


@async_test
async def test_a_sweep_run_is_not_listed_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_sweep_run(tmp_path)
    _save_scope_run(tmp_path, channels=(Channel.C1,))
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert len(screen._paths) == 1  # only the scope acquisition
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
async def test_selecting_a_run_populates_a_checkbox_per_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert set(screen._checkboxes) == {"C1", "C2"}
        assert all(cb.value for cb in screen._checkboxes.values())  # all shown by default


@async_test
async def test_unchecking_a_channel_drops_it_from_the_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, pilot):
        screen._checkboxes["C2"].value = False
        await pilot.pause()
        assert [t.label for t in screen._selected_traces()] == ["C1"]


# -- subtract ---------------------------------------------------------------------------------


@async_test
async def test_subtract_adds_a_derived_trace_and_a_checkbox_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        screen.query_one("#data-op", Select).value = "subtract"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-chan-b", Select).value = "C2"
        await pilot.pause()

        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        assert "C1 - C2" in screen._derived
        assert "C1 - C2" in screen._checkboxes
        assert "Added derived trace: C1 - C2" in _log(screen)


@async_test
async def test_subtract_with_no_channel_b_flags_that_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        app = pilot.app
        assert isinstance(app, app_mod.IyzeeApp)
        screen.query_one("#data-op", Select).value = "subtract"
        screen.query_one("#data-chan-a", Select).value = "C1"
        await pilot.pause()

        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        assert screen._derived == {}
        assert screen.query_one("#data-chan-b", Select).has_class("-invalid")
        assert any("Channel B" in n for n in notifications(app))


@async_test
async def test_reapplying_the_same_subtract_replaces_rather_than_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        screen.query_one("#data-op", Select).value = "subtract"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-chan-b", Select).value = "C2"
        await pilot.pause()
        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()
        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        assert len(screen._derived) == 1
        assert list(screen._checkboxes).count("C1 - C2") == 1


# -- background correction ---------------------------------------------------------------------


@async_test
async def test_background_region_subtracts_the_mean_of_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = DetailedFakeScope()
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,), scope=scope).parent
    ) as (screen, pilot):
        c1 = screen._measured["C1"]
        lo, hi = float(c1.time[0]), float(c1.time[min(1, len(c1.time) - 1)])

        screen.query_one("#data-op", Select).value = "background-region"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-region-lo", Input).value = f"{lo}"
        screen.query_one("#data-region-hi", Input).value = f"{hi}"
        await pilot.pause()
        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        (derived_label,) = [label for label in screen._derived if label != "C1"]
        assert "bg-corrected" in derived_label


@async_test
async def test_background_region_with_no_samples_in_window_is_a_domain_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,)).parent
    ) as (screen, pilot):
        app = pilot.app
        assert isinstance(app, app_mod.IyzeeApp)
        screen.query_one("#data-op", Select).value = "background-region"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-region-lo", Input).value = "999"
        screen.query_one("#data-region-hi", Input).value = "1000"
        await pilot.pause()

        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        assert screen._derived == {}
        assert any("no samples" in n for n in notifications(app))


# -- scale --------------------------------------------------------------------------------------


@async_test
async def test_scale_axes_applies_the_form_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,)).parent
    ) as (screen, pilot):
        screen.query_one("#data-op", Select).value = "scale"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-yscale", Input).value = "1000"
        await pilot.pause()

        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        (derived,) = screen._derived.values()
        import numpy as np

        np.testing.assert_allclose(derived.values, screen._measured["C1"].values * 1000)


@async_test
async def test_a_non_numeric_scale_field_flags_that_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(
        monkeypatch, _save_scope_run(tmp_path, channels=(Channel.C1,)).parent
    ) as (screen, pilot):
        screen.query_one("#data-op", Select).value = "scale"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-yscale", Input).value = "not a number"
        await pilot.pause()

        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        assert screen._derived == {}
        assert screen.query_one("#data-yscale", Input).has_class("-invalid")


# -- clear derived --------------------------------------------------------------------------


@async_test
async def test_clear_derived_removes_every_derived_trace_and_its_checkbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, _save_scope_run(tmp_path).parent) as (screen, pilot):
        screen.query_one("#data-op", Select).value = "subtract"
        screen.query_one("#data-chan-a", Select).value = "C1"
        screen.query_one("#data-chan-b", Select).value = "C2"
        await pilot.pause()
        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()

        screen.query_one("#data-clear-op", Button).press()
        await pilot.pause()

        assert screen._derived == {}
        assert set(screen._checkboxes) == {"C1", "C2"}
        assert "Cleared derived traces." in _log(screen)


# -- export ------------------------------------------------------------------------------------


@async_test
async def test_export_writes_a_real_png_next_to_the_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npz_path = _save_scope_run(tmp_path)
    async with _open_data_screen(monkeypatch, npz_path.parent) as (screen, pilot):
        screen.query_one("#data-export", Button).press()
        await pilot.pause()

        out = npz_path.with_name(f"{npz_path.stem}-plot.png")
        assert out.exists()
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert f"Saved plot: {out}" in _log(screen)


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


@async_test
async def test_export_before_selecting_any_run_refuses_with_a_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_data_screen(monkeypatch, tmp_path) as (screen, pilot):
        app = pilot.app
        assert isinstance(app, app_mod.IyzeeApp)

        screen.query_one("#data-export", Button).press()
        await pilot.pause()

        assert any("Select a scope acquisition" in n for n in notifications(app))


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
        screen.query_one("#data-op", Select).value = "scale"
        screen.query_one("#data-chan-a", Select).value = "C3"
        await pilot.pause()
        screen.query_one("#data-apply-op", Button).press()
        await pilot.pause()
        assert screen._derived

        list_view = screen.query_one("#data-list")
        list_view.index = 1  # the other (C1/C2) run
        await pilot.pause()

        assert screen._derived == {}
        assert set(screen._measured) == {"C1", "C2"}
