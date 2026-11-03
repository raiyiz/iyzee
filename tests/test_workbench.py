"""``iyzee.workbench``: select, score, compare, plot and save recordings (needs polars)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pl = pytest.importorskip("polars")

from iyzee.workbench import (  # noqa: E402
    Workset,
    WorksetStore,
    array_inventory,
    array_overlay,
    channels_in,
    csv_bytes,
    decimate,
    field_chart,
    figure_bytes,
    group_curves,
    group_difference,
    pick,
    scan_catalog,
    scope_traces,
)
from iyzee.workbench.demo import write_demo  # noqa: E402


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


def test_the_catalog_selects_runs_and_a_workset_scores_them(demo: Path):
    catalog = scan_catalog(demo)
    assert catalog.sort("started")["ref"].to_list() == [
        f"#{n:02d}" for n in range(1, catalog.height + 1)
    ]
    assert catalog["synthetic"].all()  # generated data always says so

    frequency = pick(catalog, kind="frequency")
    assert 0 < frequency.height < catalog.height and set(frequency["ref"]) <= set(catalog["ref"])
    assert set(pick(catalog, kind=["frequency", "scope"])["kind"]) == {"frequency", "scope"}
    assert pick(catalog, kind="scope", latest=1).height == 1

    workset = Workset.from_catalog(catalog, kind="frequency")
    board, best = workset.scoreboard(), workset.best()
    assert board.height == best.height == len(workset)
    assert board["best_db"].is_sorted()
    assert workset.delta(workset.rows["ref"][0]).equals(workset.delta())
    with pytest.raises(ValueError, match="not a frequency sweep"):
        workset.delta("#03", kind="frequency")  # #03 is a scope recording


def test_group_statistics_recover_a_known_gap_and_flag_only_real_ones():
    points, groups = _synthetic_sweeps()
    curves = group_curves(points, groups)
    assert set(curves["group"]) == {"A", "B"} and (curves["n"] == 3).all()
    assert curves.filter(pl.col("group") == "A")["rel_noise_mean_db"].mean() == pytest.approx(
        -2.0, abs=0.1
    )

    diff = group_difference(points, groups, "A", "B")
    assert diff["delta"].mean() == pytest.approx(-1.0, abs=0.15)
    assert diff["significant"].all() and (diff["delta_low"] < diff["delta"]).all()

    quiet, same = _synthetic_sweeps(offsets=(-1.0, -1.0), seed=4)
    assert group_difference(quiet, same, "A", "B")["significant"].mean() < 0.3


def test_working_sets_round_trip_through_the_store(demo: Path, tmp_path: Path):
    catalog = scan_catalog(demo)
    grouped = Workset.from_catalog(catalog, kind="frequency").with_groups("synthetic")
    store = WorksetStore(tmp_path / "config" / "worksets.json")
    store.save("  my   set  ", grouped)
    assert store.names() == ["my set"]
    back = Workset.from_saved(catalog, store.get("my set"))
    assert back.paths == grouped.paths and back.group_map == grouped.group_map
    with pytest.raises(ValueError, match="give the working set a name"):
        store.save("   ", grouped)
    assert store.delete("my set") and store.names() == []


def test_scope_traces_decimate_and_saved_arrays_overlay(demo: Path):
    scopes = pick(scan_catalog(demo), kind="scope")
    channels = channels_in(scopes)
    assert channels == ["C1", "C2", "C3"]
    traces = scope_traces(scopes, channels[:1])
    assert set(traces["channel"]) == {"C1"} and traces.height > 0

    t = np.linspace(0, 1, 20_000)
    y = np.sin(40 * t)
    y[12_345] = 5.0  # a lone spike must survive thinning
    _, small_y = decimate(t, y, budget=500)
    assert len(small_y) <= 600 and small_y.max() == 5.0

    inventory = array_inventory(scopes)
    assert {"array", "shape"} <= set(inventory.columns) and inventory.height > 0
    overlay = array_overlay(scopes, inventory["array"][0])
    assert overlay.height > 0


def test_charts_render_facet_band_bar_and_log_variants_and_export(demo: Path):
    points, _ = _synthetic_sweeps()
    data = points.with_columns(
        group=pl.col("run").str.slice(0, 1),
        low=pl.col("rel_noise_mean_db") - 0.1,
        high=pl.col("rel_noise_mean_db") + 0.1,
        x_positive=pl.col("offset_mhz") + 100,
    )
    for mark in ("line", "points"):
        figure = field_chart(data, x="offset_mhz", y="rel_noise_mean_db", mark=mark, title="t")
        assert figure_bytes(figure).startswith(b"\x89PNG")
    assert figure_bytes(figure, fmt="svg").lstrip().startswith(b"<?xml")
    assert csv_bytes(points).startswith(b"run,run_id")

    faceted = field_chart(
        data, x="offset_mhz", y="rel_noise_mean_db", color="run", detail="group",
        facet="group", band=("low", "high"),
    )  # fmt: skip
    titles = [a.get_title(loc="left") for a in faceted.axes if a.get_visible()]
    assert sorted(titles) == ["group: A", "group: B"]
    assert len(faceted.legends[0].get_texts()) == 6  # one entry per run

    logged = field_chart(data, x="x_positive", y="rel_noise_mean_db", x_log=True)
    assert logged.axes[0].get_xscale() == "log"
    paired = pl.DataFrame(
        {
            "run": ["run 1"] * 4,
            "signal": ["squeezing", "squeezing", "shot noise", "shot noise"],
            "sample": [0, 1, 0, 1],
            "value": [-40.0, -39.0, -42.0, -41.0],
        }
    )
    paired_figure = field_chart(
        paired,
        x="sample",
        y="value",
        color="run",
        detail="signal",
        faint_detail="shot noise",
    )
    lines = {line.get_label(): line for line in paired_figure.axes[0].lines}
    measured, reference = lines["run 1 · squeezing"], lines["run 1 · shot noise"]
    assert measured.get_color() == reference.get_color()
    assert measured.get_linestyle() == "-"
    assert reference.get_linestyle() == "--"
    assert reference.get_alpha() == pytest.approx(0.32)
    assert reference.get_linewidth() < measured.get_linewidth()
    assert reference.get_zorder() < measured.get_zorder()

    selected_points = pl.DataFrame(
        {
            "sweep_point": ["point A"] * 4 + ["point B"] * 4,
            "run": ["run 1"] * 8,
            "signal": ["squeezing", "squeezing", "shot noise", "shot noise"] * 2,
            "sample": [0, 1, 0, 1, 2, 5, 2, 5],
            "value": [-40.0, -39.0, -42.0, -41.0, -90.0, -85.0, -95.0, -91.0],
        }
    )
    selected_figure = field_chart(
        selected_points,
        x="sample",
        y="value",
        color="run",
        detail="signal",
        facet="sweep_point",
        faint_detail="shot noise",
    )
    selected_axes = [axis for axis in selected_figure.axes if axis.get_visible()]
    assert len(selected_axes) == 2
    assert all(len(axis.lines) == 2 for axis in selected_axes)
    for axis in selected_axes:
        assert set(line.get_label() for line in axis.lines) == {
            "run 1 · squeezing",
            "run 1 · shot noise",
        }
    first, second = selected_axes
    assert first.get_shared_x_axes().joined(first, second)
    assert first.get_shared_y_axes().joined(first, second)
    assert first.get_xlim() == second.get_xlim()
    assert first.get_ylim() == second.get_ylim()
    assert first.get_xlim()[0] <= 0 and first.get_xlim()[1] >= 5
    assert first.get_ylim()[0] <= -95 and first.get_ylim()[1] >= -39

    bars = data.group_by("group", "run").agg(pl.col("rel_noise_mean_db").mean())
    chart = field_chart(bars, x="group", y="rel_noise_mean_db", color="run", mark="bars")
    assert [t.get_text() for t in chart.axes[0].get_xticklabels()] == ["A", "B"]

    with pytest.raises(ValueError, match="not in the selected table"):
        field_chart(data, x="nope", y="rel_noise_mean_db")
    with pytest.raises(ValueError, match="no usable rows"):
        field_chart(points.clear(), x="offset_mhz", y="rel_noise_mean_db")


def test_the_demo_writer_is_repeatable(tmp_path: Path):
    first, second = tmp_path / "a", tmp_path / "b"
    assert len(write_demo(first, seed=5)) == len(write_demo(second, seed=5)) > 0
    assert scan_catalog(first).height == scan_catalog(second).height
