"""Tests for the notebook Workset (selection, refs, colours, multi-run analysis).

Run with:
    uv run --group notebooks pytest tests/test_notebook_workset.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

pl = pytest.importorskip("polars")

ROOT = Path(__file__).parents[1]
NOTEBOOKS = ROOT / "notebooks"
sys.path.insert(0, str(NOTEBOOKS))
sys.path.insert(0, str(ROOT / "scripts"))

from demo_data import write_demo  # noqa: E402
from toolbox import (  # noqa: E402
    PALETTE,
    default_selection,
    field_chart,
    figure_bytes,
    pick,
    scan_catalog,
)
from workset import Workset  # noqa: E402


@pytest.fixture(scope="module")
def demo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    write_demo(root, now=datetime(2026, 10, 19, 12, tzinfo=UTC), seed=3)
    return root


@pytest.fixture(scope="module")
def catalog(demo):
    return scan_catalog(demo)


def test_refs_are_chronological_and_independent_of_filters(catalog):
    ordered = catalog.sort("started")
    assert ordered["ref"].to_list() == [f"#{n:02d}" for n in range(1, catalog.height + 1)]
    frequency = pick(catalog, kind="frequency")
    assert set(frequency["ref"]) <= set(catalog["ref"])
    assert frequency.height < catalog.height  # the refs were not renumbered by the filter


def test_pick_combines_filters(catalog):
    assert set(pick(catalog, kind=["frequency", "scope"])["kind"]) == {"frequency", "scope"}
    assert pick(catalog, kind="all").height == catalog.height
    assert pick(catalog, synthetic=False).is_empty()
    assert pick(catalog, text="SCOPE-PULSES").height == 3
    assert pick(catalog, kind="scope", latest=1).height == 1
    newest = catalog.sort("started", descending=True)["ref"][0]
    assert pick(catalog, latest=1)["ref"][0] == newest
    start = catalog.sort("started")["started"][2].date()
    assert pick(catalog, since=start).height == catalog.height - 2
    assert pick(catalog, until=date(2000, 1, 1)).is_empty()
    assert pick(catalog, refs=["#01", "#02"]).height == 2


def test_workset_accepts_refs_names_and_paths(catalog):
    by_ref = Workset.from_catalog(catalog, ["#01", "#03"])
    assert by_ref.refs == ["#01", "#03"]
    by_name = Workset.from_catalog(catalog, ["scope-pulses-500hz"])
    assert len(by_name) == 1
    by_path = Workset.from_catalog(catalog, [catalog["path"][0]])
    assert len(by_path) == 1
    assert len(Workset.from_catalog(catalog, kind="scope")) == 3


def test_missing_selection_raises_or_is_reported(catalog):
    with pytest.raises(ValueError, match="#99"):
        Workset.from_catalog(catalog, ["#01", "#99"])
    ws = Workset.from_catalog(catalog, ["#01", "#99"], strict=False)
    assert ws.refs == ["#01"]
    assert ws.missing == ["#99"]


def test_every_table_names_runs_with_their_ref(catalog):
    ws = Workset.from_catalog(catalog, ["#02", "#05", "#03"])
    assert all(run.startswith(("#02 · ", "#05 · ")) for run in ws.points["run"])
    assert all(run.startswith("#03 · ") for run in ws.channel_stats["run"])
    assert all(
        run.startswith("#02 · ") or run.startswith("#05 · ") for run in ws.scoreboard()["run"]
    )
    assert set(ws.palette) == set(ws.rows["label"])


def test_colours_are_stable_across_subsets_and_cycle(catalog):
    ws = Workset.from_catalog(catalog)
    assert len(set(ws.palette.values())) == len(PALETTE)  # 11 runs reuse the 8 colours
    assert "colours repeat" in ws.banner()
    sub = ws.subset(kind="frequency")
    assert all(sub.palette[label] == ws.palette[label] for label in sub.palette)


def test_banner_escapes_html(catalog):
    rows = catalog.head(1).with_columns(pl.lit("<script>x</script>").alias("name"))
    banner = Workset(rows).banner()
    assert "<script>" not in banner
    assert "&lt;script&gt;" in banner
    assert "nothing selected" in Workset(catalog.head(0)).banner()


def test_scoreboard_and_best_agree(catalog):
    ws = Workset.from_catalog(catalog, kind="frequency")
    board = ws.scoreboard()
    best = ws.best()
    assert board.height == best.height == len(ws)
    assert board["best_db"].to_list() == pytest.approx(best["rel_noise_mean_db"].to_list())
    assert board["best_db"].is_sorted()
    assert (board["below_shot_noise"] >= 0).all()


def test_delta_defaults_and_validates_reference(catalog):
    ws = Workset.from_catalog(catalog, kind="frequency")
    first = ws.rows["ref"][0]
    delta = ws.delta()
    assert first not in {run.split(" · ")[0] for run in delta["run"]}
    assert ws.delta(first).equals(delta)
    with pytest.raises(ValueError, match="not a frequency sweep"):
        ws.delta("#03", kind="frequency")  # #03 is a scope recording
    mixed = Workset.from_catalog(catalog, ["#01", "#02"])
    with pytest.raises(ValueError, match="choose kind"):
        mixed.delta()


def test_point_traces_and_missing_points(catalog):
    ws = Workset.from_catalog(catalog, kind=["frequency", "bandwidth"])
    traces = ws.point_traces(0)
    assert set(traces["signal"]) == {"squeezing", "shot noise", "relative noise"}
    assert traces["run"].n_unique() == len(ws)
    assert ws.point_traces(10_000).is_empty()
    assert "has no point 10000" in ws.issues["problem"].to_list()[0]


def test_array_overlay_skips_runs_without_the_array_quietly(catalog):
    ws = Workset.from_catalog(catalog)
    assert "trace_squeezing" in ws.array_names
    overlay = ws.array("trace_squeezing", 1)
    assert overlay["run"].n_unique() == len(ws.sweeps)
    assert ws.issues.is_empty()  # scope files lacking the array are not "problems"
    assert ws.array("trace_squeezing", 9999).is_empty()
    assert not ws.issues.is_empty()  # but a row beyond the array is


def test_quick_plots_and_exports(catalog):
    ws = Workset.from_catalog(catalog)
    figures = dict(ws.quick_plots(channels=["C1", "C2"], budget=200))
    assert len(figures) == 3
    scope = figures["Scope captures"]
    assert sum(axis.get_visible() for axis in scope.axes) == 2  # one panel per channel
    assert figure_bytes(scope, "png").startswith(b"\x89PNG")
    assert figure_bytes(scope, "svg").lstrip().startswith(b"<?xml")
    with pytest.raises(ValueError, match="unsupported figure format"):
        figure_bytes(scope, "gif")
    assert b"run" in Workset.csv(ws.points)


def test_field_chart_uses_palette_facets_and_grouped_bars(catalog):
    ws = Workset.from_catalog(catalog, kind="bandwidth")
    points = ws.points
    figure = ws.plot(points, x="rbw_khz", y="rel_noise_mean_db", x_log=True)
    axis = figure.axes[0]
    assert axis.get_xscale() == "log"
    used = {line.get_color() for line in axis.get_lines() if line.get_label() in ws.palette}
    assert used == set(ws.palette.values())

    bars = ws.plot(points, x="rbw_khz", y="rel_noise_mean_db", mark="bars")
    assert len(bars.axes[0].patches) == points.drop_nulls("rel_noise_mean_db").height

    faceted = field_chart(points, x="rbw_khz", y="rel_noise_mean_db", facet="run")
    assert sum(a.get_visible() for a in faceted.axes) == len(ws)

    with pytest.raises(ValueError, match="not in the selected table"):
        field_chart(points, x="rbw_khz", y="nonsense")


def test_default_selection_picks_newest_files(demo):
    chosen = default_selection(demo, 2)
    assert chosen == [str(p.resolve()) for p in sorted(demo.rglob("*.npz"))[-2:]]
    assert default_selection(demo / "missing") == []


def test_workbench_notebook_runs_end_to_end(demo):
    """Every cell of the workbench executes against generated data (no cell may raise)."""
    pytest.importorskip("marimo")
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [sys.executable, str(NOTEBOOKS / "workbench.py")],
        env={**os.environ, "IYZEE_DATA_DIR": str(demo)},
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
