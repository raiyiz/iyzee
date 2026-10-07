"""The guide's rubidium figures are drawn from `docs/data/rubidium.json`.

That file is generated from `Rb_transitions` by `scripts/export_rb_data.py`.
These tests keep it honest: it must match the code, and the code's table must
be internally consistent with the hyperfine physics the guide describes.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_rb_data.py"
DATA = ROOT / "docs" / "data" / "rubidium.json"


def _export():
    spec = importlib.util.spec_from_file_location("export_rb_data", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def export():
    return _export()


def test_committed_data_matches_the_transition_table(export) -> None:
    assert DATA.read_text(encoding="utf-8") == export.render(export.build()), (
        "docs/data/rubidium.json is stale; run: uv run python scripts/export_rb_data.py"
    )


def test_every_transition_obeys_the_electric_dipole_selection_rule(export) -> None:
    _, rows = export.parse()
    assert len(rows) == 20
    for row in rows:
        change = row["f_excited"] - row["f_ground"]
        assert abs(change) <= 1, row["label"]
        assert (row["f_ground"], row["f_excited"]) != (0, 0)


def test_strengths_sum_to_one_for_each_ground_level(export) -> None:
    _, rows = export.parse()
    total: dict[tuple[str, int, int], float] = defaultdict(float)
    for row in rows:
        total[row["line"], row["isotope"], row["f_ground"]] += row["strength"]
    assert len(total) == 8
    for key, value in total.items():
        assert value == pytest.approx(1.0), key


def test_table_is_ground_offset_plus_excited_offset(export) -> None:
    # Each entry is centre + ground-F offset + excited-F' offset, which is what
    # lets the guide draw one level diagram per isotope.
    levels = export.build()["levels_ghz"]
    for isotope in ("85", "87"):
        assert levels[isotope]["residual_ghz"] < 1e-6  # Steck prints offsets to ~kHz


@pytest.mark.parametrize(("isotope", "splitting_ghz"), [("85", 3.035732439), ("87", 6.834682611)])
def test_ground_state_hyperfine_splitting_matches_steck(export, isotope, splitting_ghz) -> None:
    ground = export.build()["levels_ghz"][isotope]["ground"]
    assert max(ground.values()) - min(ground.values()) == pytest.approx(splitting_ghz, abs=2e-6)


def test_default_sweep_centre_is_the_rb87_d1_f2_to_f2_line(export) -> None:
    # The guide says so; keep it true. The stored default has seven decimals.
    _, rows = export.parse()
    line = next(r for r in rows if r["label"] == "D1 - Rb87_F22")
    assert line["thz"] == pytest.approx(377.1052067, abs=1e-7)
