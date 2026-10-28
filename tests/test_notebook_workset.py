"""Tests for the notebook Workset (selection, refs, colours, multi-run analysis).

Run with:
    uv run --group notebooks pytest tests/test_notebook_workset.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
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
    group_curves,
    group_difference,
    group_summary,
    pick,
    preview_facts,
    read_manifest,
    scan_catalog,
)
from workset import SavedSet, Workset, WorksetStore, default_store_path  # noqa: E402


@pytest.fixture(scope="module")
def demo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    write_demo(root, now=datetime(2026, 10, 19, 12, tzinfo=UTC), seed=3)
    return root


@pytest.fixture(scope="module")
def catalog(demo):
    return scan_catalog(demo)


def test_selection_and_refs(catalog):
    # -- refs are chronological and independent of filters
    def _refs_are_chronological_and_independent_o():
        ordered = catalog.sort("started")
        assert ordered["ref"].to_list() == [f"#{n:02d}" for n in range(1, catalog.height + 1)]
        frequency = pick(catalog, kind="frequency")
        assert set(frequency["ref"]) <= set(catalog["ref"])
        assert frequency.height < catalog.height  # the refs were not renumbered by the filter

    _refs_are_chronological_and_independent_o()

    # -- pick combines filters
    def _pick_combines_filters():
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

    _pick_combines_filters()

    # -- workset accepts refs names and paths
    def _workset_accepts_refs_names_and_paths():
        by_ref = Workset.from_catalog(catalog, ["#01", "#03"])
        assert by_ref.refs == ["#01", "#03"]
        by_name = Workset.from_catalog(catalog, ["scope-pulses-500hz"])
        assert len(by_name) == 1
        by_path = Workset.from_catalog(catalog, [catalog["path"][0]])
        assert len(by_path) == 1
        assert len(Workset.from_catalog(catalog, kind="scope")) == 3

    _workset_accepts_refs_names_and_paths()

    # -- missing selection raises or is reported
    def _missing_selection_raises_or_is_reported():
        with pytest.raises(ValueError, match="#99"):
            Workset.from_catalog(catalog, ["#01", "#99"])
        ws = Workset.from_catalog(catalog, ["#01", "#99"], strict=False)
        assert ws.refs == ["#01"]
        assert ws.missing == ["#99"]

    _missing_selection_raises_or_is_reported()


def test_tables_colours_and_banner(catalog):
    # -- every table names runs with their ref
    def _every_table_names_runs_with_their_ref():
        ws = Workset.from_catalog(catalog, ["#02", "#05", "#03"])
        assert all(run.startswith(("#02 · ", "#05 · ")) for run in ws.points["run"])
        assert all(run.startswith("#03 · ") for run in ws.channel_stats["run"])
        assert all(
            run.startswith("#02 · ") or run.startswith("#05 · ") for run in ws.scoreboard()["run"]
        )
        assert set(ws.palette) == set(ws.rows["label"])

    _every_table_names_runs_with_their_ref()

    # -- colours are stable across subsets and cycle
    def _colours_are_stable_across_subsets_and_cy():
        ws = Workset.from_catalog(catalog)
        assert len(set(ws.palette.values())) == len(PALETTE)  # 11 runs reuse the 8 colours
        assert "colours repeat" in ws.banner()
        sub = ws.subset(kind="frequency")
        assert all(sub.palette[label] == ws.palette[label] for label in sub.palette)

    _colours_are_stable_across_subsets_and_cy()

    # -- banner escapes html
    def _banner_escapes_html():
        rows = catalog.head(1).with_columns(pl.lit("<script>x</script>").alias("name"))
        banner = Workset(rows).banner()
        assert "<script>" not in banner
        assert "&lt;script&gt;" in banner
        assert "nothing selected" in Workset(catalog.head(0)).banner()

    _banner_escapes_html()


def test_scoreboard_deltas_traces_and_overlays(catalog):
    # -- scoreboard and best agree
    def _scoreboard_and_best_agree():
        ws = Workset.from_catalog(catalog, kind="frequency")
        board = ws.scoreboard()
        best = ws.best()
        assert board.height == best.height == len(ws)
        assert board["best_db"].to_list() == pytest.approx(best["rel_noise_mean_db"].to_list())
        assert board["best_db"].is_sorted()
        assert (board["below_shot_noise"] >= 0).all()

    _scoreboard_and_best_agree()

    # -- delta defaults and validates reference
    def _delta_defaults_and_validates_reference():
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

    _delta_defaults_and_validates_reference()

    # -- point traces and missing points
    def _point_traces_and_missing_points():
        ws = Workset.from_catalog(catalog, kind=["frequency", "bandwidth"])
        traces = ws.point_traces(0)
        assert set(traces["signal"]) == {"squeezing", "shot noise", "relative noise"}
        assert traces["run"].n_unique() == len(ws)
        assert ws.point_traces(10_000).is_empty()
        assert "has no point 10000" in ws.issues["problem"].to_list()[0]

    _point_traces_and_missing_points()

    # -- array overlay skips runs without the array quietly
    def _array_overlay_skips_runs_without_the_arr():
        ws = Workset.from_catalog(catalog)
        assert "trace_squeezing" in ws.array_names
        overlay = ws.array("trace_squeezing", 1)
        assert overlay["run"].n_unique() == len(ws.sweeps)
        assert ws.issues.is_empty()  # scope files lacking the array are not "problems"
        assert ws.array("trace_squeezing", 9999).is_empty()
        assert not ws.issues.is_empty()  # but a row beyond the array is

    _array_overlay_skips_runs_without_the_arr()


def test_plots_and_field_charts(catalog):
    # -- quick plots and exports
    def _quick_plots_and_exports():
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

    _quick_plots_and_exports()

    # -- field chart uses palette facets and grouped bars
    def _field_chart_uses_palette_facets_and_grou():
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

    _field_chart_uses_palette_facets_and_grou()

    # -- field chart draws bands
    def _field_chart_draws_bands():
        points, groups = _synthetic_sweeps()
        curves = group_curves(points, groups)
        figure = field_chart(
            curves,
            x="offset_mhz",
            y="rel_noise_mean_db",
            color="group",
            band=("band_low", "band_high"),
        )
        assert len(figure.axes[0].collections) == 2  # one shaded band per group
        with pytest.raises(ValueError, match="not in the selected table"):
            field_chart(curves, x="offset_mhz", y="rel_noise_mean_db", band=("nope", "band_high"))

    _field_chart_draws_bands()


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


# --- groups and statistics -----------------------------------------------------------


def _synthetic_sweeps(offsets=(-2.0, -1.0), per_group=3, seed=0, jitter=0.3):
    """Frequency sweeps with a known group difference and jittered, unequal x grids."""
    rng = np.random.default_rng(seed)
    rows, groups = [], {}
    for g, offset in zip("AB", offsets, strict=True):
        for i in range(per_group):
            x = np.linspace(-50, 50, 21) + rng.normal(0, jitter, 21)
            y = offset + 0.01 * x + rng.normal(0, 0.1, 21)
            run_id = f"{g}{i}"
            groups[run_id] = g
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


def test_groups_summarise_compare_and_survive_subsets(catalog):
    # -- group curves recover known means and spread
    def _group_curves_recover_known_means_and_spr():
        points, groups = _synthetic_sweeps()
        curves = group_curves(points, groups)
        by = {g: curves.filter(pl.col("group") == g) for g in "AB"}
        assert by["A"]["n"].unique().to_list() == [3]
        assert by["A"]["rel_noise_mean_db"].mean() == pytest.approx(-2.0, abs=0.1)
        assert by["B"]["rel_noise_mean_db"].mean() == pytest.approx(-1.0, abs=0.1)
        assert (by["A"]["band_high"] > by["A"]["band_low"]).all()
        # all groups share one x grid, so they can be compared point by point
        assert by["A"]["offset_mhz"].to_list() == by["B"]["offset_mhz"].to_list()
        sem = group_curves(points, groups, band="sem").filter(pl.col("group") == "A")
        assert sem["band_high"].mean() - sem["rel_noise_mean_db"].mean() < by["A"]["std"].mean()
        none = group_curves(points, groups, band="none")
        assert none["band_low"].null_count() == none.height
        with pytest.raises(ValueError, match="band must be"):
            group_curves(points, groups, band="wide")

    _group_curves_recover_known_means_and_spr()

    # -- single run group has a mean but no spread
    def _single_run_group_has_a_mean_but_no_sprea():
        points, _ = _synthetic_sweeps()
        solo = group_curves(points, {"A0": "solo", "A1": "pair", "A2": "pair"})
        one = solo.filter(pl.col("group") == "solo")
        assert one["n"].unique().to_list() == [1]
        assert one["std"].null_count() == one.height
        assert one["rel_noise_mean_db"].null_count() == 0

    _single_run_group_has_a_mean_but_no_sprea()

    # -- group difference flags a real gap and not noise
    def _group_difference_flags_a_real_gap_and_no():
        points, groups = _synthetic_sweeps()
        diff = group_difference(points, groups, "A", "B")
        assert diff["delta"].mean() == pytest.approx(-1.0, abs=0.15)
        assert diff["significant"].all()
        assert (diff["delta_low"] < diff["delta"]).all()
        quiet, same = _synthetic_sweeps(offsets=(-1.0, -1.0), seed=4)
        assert group_difference(quiet, same, "A", "B")["significant"].mean() < 0.3
        assert group_difference(points, groups, "B", "A")["delta"].mean() == pytest.approx(
            -diff["delta"].mean()
        )
        with pytest.raises(ValueError, match="no usable runs"):
            group_difference(points, groups, "A", "nope")

    _group_difference_flags_a_real_gap_and_no()

    # -- group difference needs two runs per group to flag anything
    def _group_difference_needs_two_runs_per_grou():
        points, groups = _synthetic_sweeps(per_group=1)
        diff = group_difference(points, groups, "A", "B")
        assert diff["se"].null_count() == diff.height
        assert not diff["significant"].any()

    _group_difference_needs_two_runs_per_grou()

    # -- group curves ignore ungrouped runs and handle empty input
    def _group_curves_ignore_ungrouped_runs_and_h():
        points, groups = _synthetic_sweeps()
        assert group_curves(points, {}).is_empty()
        assert group_curves(points, {"A0": "x", "A1": "x"})["group"].unique().to_list() == ["x"]
        assert group_summary(pl.DataFrame(), groups).is_empty()

    _group_curves_ignore_ungrouped_runs_and_h()

    # -- with groups accepts every documented form
    def _with_groups_accepts_every_documented_for():
        ws = Workset.from_catalog(catalog, kind="frequency")
        a, b, c = ws.refs[:3]
        by_run = ws.with_groups({a: "base", b: "pumped", c: "pumped"})
        by_group = ws.with_groups({"base": [a], "pumped": [b, c]})
        assert by_run.group_map == by_group.group_map
        assert by_run.group_names == ["base", "pumped"]
        assert ws.with_groups("kind").group_names == ["frequency"]
        assert ws.with_groups(lambda row: "first" if row["ref"] == a else None).group_names == [
            "first"
        ]
        assert ws.with_groups({a: "  ", b: "x"}).group_names == ["x"]  # blank means no group
        assert ws.group_map == {}  # the original is untouched
        assert by_run.groups["group"].to_list() == ["base", "pumped", "pumped"]
        with pytest.raises(ValueError, match="not in this working set"):
            ws.with_groups({"#99": "x"})
        with pytest.raises(ValueError, match="unknown column"):
            ws.with_groups("nonsense")

    _with_groups_accepts_every_documented_for()

    # -- groups survive subset and drive plots and summary
    def _groups_survive_subset_and_drive_plots_an():
        ws = Workset.from_catalog(catalog, kind="frequency")
        a, b, c, d = ws.refs[:4]
        grouped = ws.with_groups({a: "base", b: "base", c: "pumped", d: "pumped"})
        assert grouped.subset(kind="frequency").group_map == grouped.group_map
        only = grouped.subset(refs=[a, c])
        assert set(only.group_map) == {
            grouped.rows.filter(pl.col("ref") == r)["run_id"][0] for r in (a, c)
        }
        summary = grouped.group_summary()
        assert summary["runs"].to_list() == [2, 2]
        figure = grouped.plot_groups()
        assert [t.get_text() for t in figure.legends[0].get_texts()] == [
            "base (n=2)",
            "pumped (n=2)",
        ]
        assert len(grouped.plot_group_diff("base", "pumped").axes) == 1
        assert grouped.group_colors["base"] != grouped.group_colors["pumped"]
        solo = ws.with_groups({a: "solo"}).plot_groups()  # one run: a mean line, no band
        assert [t.get_text() for t in solo.legends[0].get_texts()] == ["solo (n=1)"]
        assert not solo.axes[0].collections
        with pytest.raises(ValueError, match="no grouped sweep has usable points"):
            ws.plot_groups()

    _groups_survive_subset_and_drive_plots_an()


# --- saved sets ----------------------------------------------------------------------


def test_the_store_round_trips_validates_and_survives_damage(catalog, tmp_path, monkeypatch):
    # -- store round trip keeps runs and groups
    def _store_round_trip_keeps_runs_and_groups():
        store = WorksetStore(tmp_path / "config" / "worksets.json")
        assert store.names() == [] and store.problem is None
        ws = Workset.from_catalog(catalog, kind="frequency").with_groups("synthetic")
        entry = store.save("  my   set  ", ws)
        assert entry.name == "my set"
        assert store.names() == ["my set"]
        assert store.describe().row(0, named=True)["runs"] == len(ws)
        back = Workset.from_saved(catalog, store.get("my set"))
        assert back.paths == ws.paths and back.group_map == ws.group_map and back.missing == []
        assert not list((tmp_path / "config").glob("*.tmp"))
        assert store.delete("my set") and not store.delete("my set")
        assert store.names() == []

    _store_round_trip_keeps_runs_and_groups()

    # -- store rejects bad names and empty sets
    def _store_rejects_bad_names_and_empty_sets():
        store = WorksetStore(tmp_path / "w.json")
        ws = Workset.from_catalog(catalog, kind="scope")
        with pytest.raises(ValueError, match="give the working set a name"):
            store.save("   ", ws)
        with pytest.raises(ValueError, match="limited to 80"):
            store.save("x" * 81, ws)
        with pytest.raises(ValueError, match="empty"):
            store.save("ok", ws.subset(kind="frequency"))
        with pytest.raises(KeyError, match="no saved working set"):
            store.get("missing")
        assert not (tmp_path / "w.json").exists()

    _store_rejects_bad_names_and_empty_sets()

    # -- store overwrites by name and drops groups of absent runs
    def _store_overwrites_by_name_and_drops_group():
        store = WorksetStore(tmp_path / "w.json")
        ws = Workset.from_catalog(catalog, kind="frequency")
        store.save("s", ws.subset(refs=ws.refs[:2]))
        entry = store.save(
            "s", ws.subset(refs=ws.refs[:3]), groups={"not-a-run": "x", ws.rows["run_id"][0]: "g"}
        )
        assert store.names() == ["s"] and len(entry.runs) == 3
        assert list(entry.groups.values()) == ["g"]

    _store_overwrites_by_name_and_drops_group()

    # -- saved set finds moved files and reports missing ones
    def _saved_set_finds_moved_files_and_reports_():
        store = WorksetStore(tmp_path / "w.json")
        store.save("s", Workset.from_catalog(catalog, kind="frequency"))
        saved = store.get("s")
        saved.runs[0]["path"] = "/moved/elsewhere.npz"  # path changed, run id still matches
        saved.runs[1].update(path="/gone/1.npz", run_id="deleted")
        ws = Workset.from_saved(catalog, saved)
        assert len(ws) == len(saved.runs) - 1
        assert len(ws.missing) == 1 and saved.runs[1]["ref"] in ws.missing[0]
        with pytest.raises(ValueError, match="not in the catalog"):
            Workset.from_saved(catalog, saved, strict=True)
        assert isinstance(saved, SavedSet) and "SavedSet" in repr(saved)

    _saved_set_finds_moved_files_and_reports_()

    # -- corrupt store is set aside not overwritten
    def _corrupt_store_is_set_aside_not_overwritt():
        path = tmp_path / "w.json"
        path.write_text("{not json")
        store = WorksetStore(path)
        assert store.names() == [] and "cannot read" in store.problem
        store.save("fresh", Workset.from_catalog(catalog, kind="scope"))
        assert (tmp_path / "w.json.corrupt").read_text() == "{not json"
        assert store.names() == ["fresh"]
        path.write_text(json.dumps({"hello": "world"}))
        assert store.names() == [] and "not a saved-sets file" in store.problem

    _corrupt_store_is_set_aside_not_overwritt()

    # -- default store follows the config dir and env override
    def _default_store_follows_the_config_dir_and():
        monkeypatch.delenv("IYZEE_WORKSETS_FILE", raising=False)
        monkeypatch.setenv("IYZEE_CONFIG", str(tmp_path / "cfg" / "config.toml"))
        assert default_store_path() == tmp_path / "cfg" / "worksets.json"
        monkeypatch.setenv("IYZEE_WORKSETS_FILE", str(tmp_path / "mine.json"))
        assert default_store_path() == tmp_path / "mine.json"

    _default_store_follows_the_config_dir_and()


# --- selection polish ----------------------------------------------------------------


def test_preview_facts_and_manifest(catalog):
    row = catalog.row(0, named=True)
    facts = dict(preview_facts(row))
    assert row["ref"] in facts["Reference"] and "SYNTHETIC" in facts["Type"]
    assert facts["File"] == row["path"]
    assert isinstance(read_manifest(row["path"]), dict)
    assert read_manifest(row["path"] + ".missing") == {}


# --- the notebook, driven through its real button handlers ---------------------------


def _boot_workbench(demo, monkeypatch, store_file):
    monkeypatch.setenv("IYZEE_DATA_DIR", str(demo))
    monkeypatch.setenv("IYZEE_WORKSETS_FILE", str(store_file))
    spec = importlib.util.spec_from_file_location(
        f"workbench_{id(store_file)}", NOTEBOOKS / "workbench.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app.run()[1]


def _click(button):
    button._on_click(None)


def test_the_workbench_buttons_keep_the_working_set_consistent(demo, monkeypatch, tmp_path):
    # -- workbench selection buttons keep the working set consistent
    def _workbench_selection_buttons_keep_the_wor():
        ui = _boot_workbench(demo, monkeypatch, tmp_path / "w.json")
        catalog, current, table = ui["catalog"], ui["get_selection"], ui["catalog_table"]

        def refs():
            return sorted(catalog.filter(pl.col("path").is_in(current()))["ref"])

        assert len(refs()) == 3  # starts with the newest three
        _click(ui["clear_set"])
        assert refs() == []
        table._update(["0", "2"])
        ticked = sorted(table.value["ref"].to_list())
        _click(ui["add_ticked"])
        _click(ui["add_ticked"])  # adding twice must not duplicate
        assert refs() == ticked and len(current()) == 2
        _click(ui["add_newest"])
        assert len(refs()) >= 3
        _click(ui["replace_ticked"])
        assert refs() == ticked
        _click(ui["add_shown"])
        assert len(current()) == ui["visible"].height
        ui["ws_table"]._update(["0"])
        before = len(current())
        _click(ui["remove_ticked"])
        assert len(current()) == before - 1

    _workbench_selection_buttons_keep_the_wor()

    # -- workbench fill and clear groups
    def _workbench_fill_and_clear_groups():
        ui = _boot_workbench(demo, monkeypatch, tmp_path / "w.json")
        ui["auto_by"]._update(["type"])
        _click(ui["fill_groups"])
        assert set(ui["group_store"].values()) <= {"frequency", "scope", "bandwidth"}
        assert ui["group_store"]
        _click(ui["clear_groups"])
        assert ui["group_store"] == {}

    _workbench_fill_and_clear_groups()

    # -- workbench table hover shows the full facts on the ref cell
    def _workbench_table_hover_shows_the_full_fac():
        ui = _boot_workbench(demo, monkeypatch, tmp_path / "w.json")
        table, visible = ui["catalog_table"], ui["visible"]
        ref = visible["ref"][0]
        hover = table._hover_cell("0", "ref", ref)
        assert ref in hover and "Instrument:" in hover and "Checksum:" in hover
        assert table._hover_cell("0", "name", "whatever") is None

    _workbench_table_hover_shows_the_full_fac()


def test_workbench_saved_sets_round_trip_through_the_buttons(demo, monkeypatch, tmp_path):
    store_file = tmp_path / "w.json"
    ui = _boot_workbench(demo, monkeypatch, store_file)
    editor = ui["group_editor"]
    names = ["base", "pumped", "pumped"]
    editor._on_change(editor.value.with_columns(pl.Series("group", names)))
    assert sorted(ui["group_store"].values()) == sorted(names)

    _click(ui["save_set"])
    assert "give the working set a name" in ui["get_notice"]()
    ui["set_name"]._update("October pairs")
    _click(ui["save_set"])
    assert "Saved" in ui["get_notice"]()
    saved = json.loads(store_file.read_text())["sets"]["October pairs"]
    assert len(saved["runs"]) == 3 and len(saved["groups"]) == 3

    again = _boot_workbench(demo, monkeypatch, store_file)  # a fresh session sees the file
    assert list(again["saved_pick"].options) == ["October pairs"]
    _click(again["clear_set"])
    again["group_store"].clear()
    _click(again["load_set"])
    assert len(again["get_selection"]()) == 3
    assert sorted(set(again["group_store"].values())) == ["base", "pumped"]

    _click(again["delete_set"])
    assert "confirm" in again["get_notice"]() and store_file.exists()
    assert "October pairs" in json.loads(store_file.read_text())["sets"]
    again["confirm_delete"]._update(True)
    _click(again["delete_set"])
    assert json.loads(store_file.read_text())["sets"] == {}
