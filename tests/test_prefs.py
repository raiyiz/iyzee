from __future__ import annotations

from pathlib import Path

from iyzee.tui.prefs import Prefs


def test_prefs_round_trip_through_the_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "tui.json"
    Prefs(path).set("results_list_width", 45)
    assert Prefs(path).get("results_list_width") == 45
    assert Prefs(path).get("missing", "fallback") == "fallback"


def test_prefs_tolerate_a_missing_or_corrupt_file(tmp_path: Path) -> None:
    assert Prefs(tmp_path / "absent.json").get("x") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    prefs = Prefs(bad)
    assert prefs.get("x") is None
    prefs.set("x", 1)  # and a later save repairs it
    assert Prefs(bad).get("x") == 1


def test_prefs_without_a_path_stay_in_memory() -> None:
    prefs = Prefs()
    prefs.set("k", "v")
    assert prefs.get("k") == "v"
