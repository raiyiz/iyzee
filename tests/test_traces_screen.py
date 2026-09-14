"""Tests for TracesScreen's archive loading and listing.

These write real .npz fixtures with iyzee.experiment.persistence rather
than hand-rolling the archive format, so the tests track the actual
on-disk schema instead of a guess at it. _DATA_ROOT is monkeypatched to
a tmp_path for every test — nothing here touches the package's real
data/ directory.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np

from iyzee.experiment import StepResult, save_step_results
from iyzee.tui import app as app_mod
from iyzee.tui.screens import traces as traces_mod
from iyzee.tui.screens.traces import TracesScreen


def _write_good_run(root: Path) -> Path:
    run_dir = root / "2026-09-13_bandwidth"
    run_dir.mkdir(parents=True)
    results = [
        StepResult(
            label="pt0",
            x_value=20_000.0,
            x_unit="Hz",
            traces={"squeezing": [1.0, 2.0], "shot_noise": [1.5, 1.5]},
            meta={"rbw_hz": 20_000.0},
        ),
        StepResult(
            label="pt1",
            x_value=40_000.0,
            x_unit="Hz",
            traces={"squeezing": [0.9, 1.8], "shot_noise": [1.4, 1.4]},
            meta={"rbw_hz": 40_000.0},
        ),
    ]
    return save_step_results(results, run_dir, run_metadata={"analyzer": "MXA"})


def _write_corrupt_run(root: Path) -> Path:
    run_dir = root / "2026-09-13_broken"
    run_dir.mkdir(parents=True)
    path = run_dir / "20260913T000000.npz"
    # Not a real .npz at all — simulates a truncated/interrupted save,
    # which is the realistic way a file like this ends up on disk.
    path.write_bytes(b"not actually a numpy archive")
    return path


@asynccontextmanager
async def _open_traces_screen(monkeypatch, data_root: Path) -> AsyncIterator[TracesScreen]:
    """Boot a real IyzeeApp, switch to the Traces screen, and yield it.

    An async context manager (rather than returning a raw
    ``app.run_test()`` handle for the caller to juggle) so the exact
    generic type Textual gives that context manager never has to be
    spelled out here — it's fully contained, and every caller gets back
    a screen already narrowed to ``TracesScreen``.
    """
    monkeypatch.setattr(traces_mod, "_DATA_ROOT", data_root)
    app = app_mod.IyzeeApp()
    async with app.run_test() as pilot:
        await pilot.press("t")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TracesScreen)
        yield screen


def test_lists_runs_newest_first(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        older = _write_good_run(tmp_path)
        newer_root = tmp_path / "2026-09-14_frequency"
        newer_root.mkdir()
        newer = save_step_results(
            [StepResult(label="pt0", x_value=1.0, x_unit="THz", traces={}, meta={})],
            newer_root,
        )
        # Make the mtimes unambiguous regardless of filesystem timestamp
        # resolution.
        os.utime(older, (time.time() - 100, time.time() - 100))
        os.utime(newer, (time.time(), time.time()))

        async with _open_traces_screen(monkeypatch, tmp_path) as screen:
            assert screen._paths[0] == newer
            assert screen._paths[1] == older

    asyncio.run(scenario())


def test_selecting_a_valid_run_shows_its_summary(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        _write_good_run(tmp_path)
        async with _open_traces_screen(monkeypatch, tmp_path) as screen:
            screen._show(screen._paths[0])
            summary = screen.query_one("#traces-summary")
            text = summary.render()
            assert "2 point(s)" in str(text)
            assert "analyzer" in str(text)

    asyncio.run(scenario())


def test_selecting_a_corrupt_run_shows_an_error_instead_of_crashing(tmp_path, monkeypatch) -> None:
    """Regression test: this is exactly the code path that shipped with a
    Python-2-style `except ValueError, TypeError:` clause — actually
    valid Python 3.14 syntax under PEP 758, so it was never broken, but
    the underlying behavior it's guarding is still worth locking down. A
    corrupt/partial .npz on disk (e.g. from an interrupted save) must
    degrade to a visible error message, not a crash."""

    async def scenario() -> None:
        path = _write_corrupt_run(tmp_path)
        async with _open_traces_screen(monkeypatch, tmp_path) as screen:
            screen._show(path)  # must not raise
            summary = screen.query_one("#traces-summary")
            text = str(summary.render())
            assert "Could not read file" in text

    asyncio.run(scenario())


def test_run_with_unparseable_metadata_still_shows_point_count(tmp_path, monkeypatch) -> None:
    """run_metadata that isn't valid JSON must not blow up the summary —
    only the metadata lines are skipped, the point count still renders."""

    async def scenario() -> None:
        run_dir = tmp_path / "2026-09-13_badmeta"
        run_dir.mkdir()
        path = save_step_results(
            [StepResult(label="pt0", x_value=1.0, x_unit="Hz", traces={}, meta={})],
            run_dir,
        )
        # Overwrite with metadata that fails json.loads, simulating a
        # hand-edited or partially-written archive.
        with np.load(path, allow_pickle=True) as archive:
            data = archive["data"]
            metadata = archive["metadata"]
        np.savez_compressed(
            path, data=data, metadata=metadata, run_metadata=np.asarray("{not json")
        )

        async with _open_traces_screen(monkeypatch, tmp_path) as screen:
            screen._show(path)  # must not raise
            summary = screen.query_one("#traces-summary")
            text = str(summary.render())
            assert "1 point(s)" in text

    asyncio.run(scenario())
