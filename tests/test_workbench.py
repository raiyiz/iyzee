"""Analysis library tests: catalog selection, run scoring, group statistics, saved sets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from iyzee.workbench import Workset, WorksetStore, group_difference, pick, scan_catalog
from iyzee.workbench.demo import write_demo


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("workbench-demo")
    write_demo(root, now=datetime(2026, 10, 19, 12, tzinfo=UTC), seed=3)
    return root


def _synthetic_sweeps(offsets=(-2.0, -1.0), per_group=3, seed=0):
    """Frequency sweeps with a known group difference and jittered, unequal x grids."""
    rng = np.random.default_rng(seed)
    rows, groups = [], {}
    for group, offset in zip("AB", offsets, strict=True):
        for i in range(per_group):
            x = np.linspace(-50, 50, 21) + rng.normal(0, 0.3, 21)
            y = offset + 0.01 * x + rng.normal(0, 0.1, 21)
            run_id = f"{group}{i}"
            groups[run_id] = group
            rows += [
                {
                    "run": run_id,
                    "run_id": run_id,
                    "kind": "frequency",
                    "synthetic": True,
                    "offset_mhz": float(a),
                    "rel_noise_mean_db": float(b),
                }
                for a, b in zip(x, y, strict=True)
            ]
    return pl.DataFrame(rows), groups


def test_workbench_selects_scores_compares_groups_and_saves_working_sets(
    demo: Path, tmp_path: Path
):
    catalog = scan_catalog(demo)
    assert catalog.sort("started")["ref"].to_list() == [
        f"#{n:02d}" for n in range(1, catalog.height + 1)
    ]

    # Selection: filters combine, and refs keep their numbers across filters.
    frequency = pick(catalog, kind="frequency")
    assert 0 < frequency.height < catalog.height and set(frequency["ref"]) <= set(catalog["ref"])
    assert set(pick(catalog, kind=["frequency", "scope"])["kind"]) == {"frequency", "scope"}
    assert pick(catalog, kind="scope", latest=1).height == 1

    # A working set scores its runs: the scoreboard and best points agree, deltas need a reference.
    workset = Workset.from_catalog(catalog, kind="frequency")
    board, best = workset.scoreboard(), workset.best()
    assert board.height == best.height == len(workset)
    assert board["best_db"].is_sorted()
    assert workset.delta(workset.rows["ref"][0]).equals(workset.delta())
    with pytest.raises(ValueError, match="not a frequency sweep"):
        workset.delta("#03", kind="frequency")  # #03 is a scope recording

    # Groups: a real gap is flagged (with an interval around it), identical groups are not.
    points, groups = _synthetic_sweeps()
    diff = group_difference(points, groups, "A", "B")
    assert diff["delta"].mean() == pytest.approx(-1.0, abs=0.15)
    assert diff["significant"].all()
    assert all(row["delta_low"] < row["delta"] for row in diff.iter_rows(named=True))
    quiet, same = _synthetic_sweeps(offsets=(-1.0, -1.0), seed=4)
    assert group_difference(quiet, same, "A", "B")["significant"].mean() < 0.3

    # Saved sets round-trip through the store, with their groups.
    store = WorksetStore(tmp_path / "config" / "worksets.json")
    grouped = workset.with_groups("synthetic")
    store.save("  my   set  ", grouped)
    assert store.names() == ["my set"]
    back = Workset.from_saved(catalog, store.get("my set"))
    assert back.paths == grouped.paths and back.group_map == grouped.group_map
    with pytest.raises(ValueError, match="give the working set a name"):
        store.save("   ", grouped)
    assert store.delete("my set") and store.names() == []
