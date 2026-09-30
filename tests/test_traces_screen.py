"""Tests for the Traces page: the run list, the preview, and how it degrades.

``traces._DATA_ROOT`` is monkeypatched to a temp directory so these never
touch the real ``data/`` directory.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import pytest
from helpers import async_test, make_result, plain, save_run, wait_until
from textual.pilot import Pilot
from textual.widgets import Label, ListView, Static

from iyzee.experiment import save_step_results
from iyzee.tui import app as app_mod
from iyzee.tui.screens import traces as traces_mod
from iyzee.tui.screens.traces import TracesScreen


@asynccontextmanager
async def _open_traces_screen(
    monkeypatch: pytest.MonkeyPatch, data_root: Path
) -> AsyncIterator[tuple[TracesScreen, Pilot]]:
    """Boot a real IyzeeApp, switch to the Traces page, and yield it (with the pilot).

    An async context manager, rather than returning a raw ``app.run_test()``
    handle, so the exact generic type Textual gives that handle never has to
    be spelled out here.
    """
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", data_root)
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("t")
        await pilot.pause(0.3)
        yield app.query_one(TracesScreen), pilot


def _summary(screen: TracesScreen) -> str:
    return plain(screen.query_one("#traces-summary", Static))


def _write_corrupt_run(root: Path) -> Path:
    run_dir = root / "2026-09"
    run_dir.mkdir(parents=True)
    path = run_dir / "13T000000_broken_abc123.npz"
    # Not a real .npz at all — simulates a truncated/interrupted save, which
    # is the realistic way a file like this ends up on disk.
    path.write_bytes(b"not actually a numpy archive")
    return path


def _write_badmeta_run(root: Path) -> Path:
    path = save_run(root, "2026-09")
    # Metadata that fails json.loads: a hand-edited or partially written sidecar.
    path.with_suffix(".json").write_text("{not json")
    return path


@async_test
async def test_lists_runs_newest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    older = save_run(tmp_path, "2026-09-13_bandwidth", mtime=1_000)
    newer = save_run(tmp_path, "2026-09-14_frequency", mtime=2_000)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert screen._paths == [newer, older]


@async_test
async def test_a_slow_load_does_not_clobber_a_newer_ones_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the generation check in _apply_view: select a
    second run while the first (now-stale) load is still reading its file,
    and confirm the plot itself ends up drawn from the second selection,
    not whichever load happened to finish first.
    """
    import threading

    old = save_run(tmp_path, "2026-09-17_bandwidth", mtime=1_000)
    save_run(tmp_path, "2026-09-18_frequency", mtime=2_000)

    real_prepare = traces_mod._prepare_view
    calls: list[Path] = []
    release = threading.Event()
    entered_first = threading.Event()

    def gated_prepare(path: Path):  # type: ignore[no-untyped-def]
        calls.append(path)
        if len(calls) == 1:
            entered_first.set()
            release.wait(timeout=5)
        return real_prepare(path)

    monkeypatch.setattr(traces_mod, "_prepare_view", gated_prepare)

    drawn: list[str] = []
    real_draw_series = traces_mod.draw_series

    def spying_draw_series(plot, series, **kwargs):  # type: ignore[no-untyped-def]
        drawn.append(kwargs.get("title", ""))
        return real_draw_series(plot, series, **kwargs)

    monkeypatch.setattr(traces_mod, "draw_series", spying_draw_series)

    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        # the initial auto-select (of `new`) is now blocked inside gated_prepare
        await wait_until(pilot, entered_first.is_set)

        screen._select(old)  # a second, newer selection while the first is still loading
        await wait_until(pilot, lambda: len(calls) == 2)
        release.set()  # let the first (now stale) load proceed and finish

        await wait_until(pilot, lambda: len(drawn) >= 1)
        await pilot.pause(0.2)  # give a stale, wrongly-applied draw a chance to land

        assert drawn == [old.name]


@async_test
async def test_reselecting_the_currently_shown_run_does_not_reload_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ListView posts Highlighted on changes it makes to itself (e.g. the
    implicit None -> 0 a freshly-populated list makes), not just on a real
    navigation — _select()'s same-path check is what keeps that from
    reloading the file and rebuilding the plot for no reason."""
    path = save_run(tmp_path, "2026-09-17_bandwidth")
    calls = 0
    real_prepare = traces_mod._prepare_view

    def counting_prepare(p: Path):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real_prepare(p)

    monkeypatch.setattr(traces_mod, "_prepare_view", counting_prepare)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        await wait_until(pilot, lambda: calls >= 1)
        first_count = calls

        screen._select(path)  # already showing this run
        await pilot.pause(0.2)

        assert calls == first_count  # no redundant reload


@async_test
async def test_a_run_shows_its_summary_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = save_run(
        tmp_path,
        "2026-09-13_bandwidth",
        make_result("pt0", 20_000.0, rbw_hz=20_000.0),
        make_result("pt1", 40_000.0, rbw_hz=40_000.0),
        analyzer="MXA",
        # [/...] is a closing tag and [nan, ...] a lowercase tag: markup would
        # raise, or silently swallow the text.
        note="see [/docs] and [nan, nan]",
    )
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        screen._path = None  # force a real (re)load of `path` below
        screen._select(path)
        await wait_until(pilot, lambda: "2 point(s)" in _summary(screen))
        text = _summary(screen)
        assert "2 point(s)" in text and "analyzer" in text
        assert "see [/docs] and [nan, nan]" in text


@pytest.mark.parametrize(
    "make_run,expected",
    [
        (_write_corrupt_run, "Could not read file"),  # an interrupted save must not crash the page
        (_write_badmeta_run, "1 point(s)"),  # only the metadata lines are skipped
    ],
)
@async_test
async def test_a_damaged_run_degrades_to_a_message_instead_of_crashing(
    make_run: Callable[[Path], Path],
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_run(tmp_path)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        screen._path = None  # force a real (re)load of `path` below
        screen._select(path)  # must not raise
        await wait_until(pilot, lambda: expected in _summary(screen))


@async_test
async def test_preview_follows_the_highlight_and_survives_a_revisit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = save_run(tmp_path, "2026-09-17_bandwidth", mtime=1_000)
    new = save_run(tmp_path, "2026-09-18_frequency", mtime=2_000)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        # On arrival the newest run is highlighted *and* previewed.
        await wait_until(pilot, lambda: new.name in _summary(screen))

        listing = screen.query_one("#traces-list", ListView)
        listing.focus()
        await pilot.press("down")  # no Enter needed
        await wait_until(pilot, lambda: listing.index == 1 and old.name in _summary(screen))
        shown = _summary(screen)
        await pilot.press("enter")  # selecting explicitly (Enter / click) still works
        await pilot.pause(0.2)
        assert _summary(screen) == shown

        await pilot.press("escape", "c")  # leave and come back
        await pilot.pause()
        await pilot.press("t")
        # Used to snap back to row 0 while the preview still showed row 1.
        await wait_until(pilot, lambda: listing.index == 1 and _summary(screen) == shown)


@async_test
async def test_an_empty_folder_explains_itself_and_names_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "No recordings yet" in _summary(screen)
        assert str(tmp_path) in plain(screen.query_one("#traces-hint", Static))


@async_test
async def test_the_list_shows_the_run_name_and_save_time_not_the_raw_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "2026-09"
    run.mkdir()
    saved = save_step_results([make_result()], run, name="bandwidth")
    when = datetime(2026, 9, 18, 14, 10, 5).timestamp()
    os.utime(saved, (when, when))
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        item = screen.query_one("#traces-list", ListView).children[0]
        assert item.query_one(Label).render().plain == "bandwidth  2026-09-18 14:10:05"  # type: ignore[union-attr]
