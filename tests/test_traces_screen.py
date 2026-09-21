"""Tests for the Traces page: the run list, the preview, and how it degrades.

``traces._DATA_ROOT`` is monkeypatched to a temp directory so these never
touch the real ``data/`` directory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
from helpers import async_test, make_result, plain, save_run
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
    run_dir = root / "2026-09-13_broken"
    run_dir.mkdir(parents=True)
    path = run_dir / "20260913T000000.npz"
    # Not a real .npz at all — simulates a truncated/interrupted save, which
    # is the realistic way a file like this ends up on disk.
    path.write_bytes(b"not actually a numpy archive")
    return path


def _write_badmeta_run(root: Path) -> Path:
    path = save_run(root, "2026-09-13_badmeta")
    with np.load(path, allow_pickle=True) as archive:
        data, metadata = archive["data"], archive["metadata"]
    # Metadata that fails json.loads: a hand-edited or partially written archive.
    np.savez_compressed(path, data=data, metadata=metadata, run_metadata=np.asarray("{not json"))
    return path


@async_test
async def test_lists_runs_newest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    older = save_run(tmp_path, "2026-09-13_bandwidth", mtime=1_000)
    newer = save_run(tmp_path, "2026-09-14_frequency", mtime=2_000)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert screen._paths == [newer, older]


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
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        screen._show(path)
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
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        screen._show(path)  # must not raise
        assert expected in _summary(screen)


@async_test
async def test_preview_follows_the_highlight_and_survives_a_revisit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = save_run(tmp_path, "2026-09-17_bandwidth", mtime=1_000)
    new = save_run(tmp_path, "2026-09-18_frequency", mtime=2_000)
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, pilot):
        # On arrival the newest run is highlighted *and* previewed.
        assert new.name in _summary(screen)

        listing = screen.query_one("#traces-list", ListView)
        listing.focus()
        await pilot.press("down")  # no Enter needed
        await pilot.pause(0.3)
        assert listing.index == 1 and old.name in _summary(screen)
        shown = _summary(screen)
        await pilot.press("enter")  # selecting explicitly (Enter / click) still works
        await pilot.pause(0.2)
        assert _summary(screen) == shown

        await pilot.press("escape", "c")  # leave and come back
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause(0.5)
        # Used to snap back to row 0 while the preview still showed row 1.
        assert listing.index == 1 and _summary(screen) == shown


@async_test
async def test_an_empty_folder_explains_itself_and_names_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        assert "No runs recorded yet" in _summary(screen)
        assert str(tmp_path) in plain(screen.query_one("#traces-hint", Static))


@async_test
async def test_the_list_shows_the_time_of_day_not_the_raw_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "2026-09-18_bandwidth"
    run.mkdir()
    saved = save_step_results([make_result()], run)
    saved.rename(run / "20260918T141005.npz")
    async with _open_traces_screen(monkeypatch, tmp_path) as (screen, _pilot):
        item = screen.query_one("#traces-list", ListView).children[0]
        assert item.query_one(Label).render().plain == "2026-09-18_bandwidth  14:10:05"  # type: ignore[union-attr]
