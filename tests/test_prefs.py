from __future__ import annotations

from pathlib import Path

from iyzee.tui.prefs import Prefs


def test_prefs_round_trip_tolerate_bad_files_and_stay_in_memory_without_a_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "tui.json"
    Prefs(path).set("results_list_width", 45)
    assert Prefs(path).get("results_list_width") == 45
    assert Prefs(path).get("missing", "fallback") == "fallback"

    assert Prefs(tmp_path / "absent.json").get("x") is None  # a missing file is fine
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    prefs = Prefs(bad)
    assert prefs.get("x") is None
    prefs.set("x", 1)  # and a later save repairs it
    assert Prefs(bad).get("x") == 1

    memory = Prefs()
    memory.set("k", "v")
    assert memory.get("k") == "v"
