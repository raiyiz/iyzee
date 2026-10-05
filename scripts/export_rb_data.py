#!/usr/bin/env python3
"""Export the rubidium reference data the documentation figures are drawn from.

`docs/chapters/rubidium-physics.typ` plots the transition table, the hyperfine
level diagram and an illustrative Doppler-broadened spectrum. All of them read
`docs/data/rubidium.json`, which this script writes from the same
`iyzee.devices.wavemeter.Rb_transitions` table the application uses, so the
guide cannot drift from the code. `tests/test_docs_data.py` fails when the
committed file is stale; regenerate it with

    uv run python scripts/export_rb_data.py

The relative hyperfine transition strengths are Steck's S_FF' factors
(Rubidium 85 D Line Data, rev. 2.3.4, Table 8; Rubidium 87 D Line Data,
rev. 1.6, Table 8). They are not part of the application, which only needs
line positions.
"""

from __future__ import annotations

import json
import re
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "data" / "rubidium.json"
sys.path.insert(0, str(ROOT / "src"))

from iyzee.devices.wavemeter import Rb_transitions  # noqa: E402

LABEL = re.compile(r"^(?P<line>D[12]) - Rb(?P<isotope>85|87)_F(?P<ground>\d)(?P<excited>\d)$")
CENTER = re.compile(r"^Rb(?P<isotope>85|87)_(?P<line>D[12])_center$")

# Nuclear spin I, and the allowed ground / excited F for each D line.
NUCLEAR_SPIN = {85: Fraction(5, 2), 87: Fraction(3, 2)}

# Steck's relative hyperfine transition strengths S_FF' (sum over F' is 1 for each F).
STRENGTH: dict[tuple[str, int, int, int], Fraction] = {
    **{
        ("D2", 85, g, e): Fraction(s)
        for (g, e), s in {
            (3, 4): "9/14",
            (3, 3): "5/18",
            (3, 2): "5/63",
            (2, 3): "14/45",
            (2, 2): "7/18",
            (2, 1): "3/10",
        }.items()
    },
    **{
        ("D1", 85, g, e): Fraction(s)
        for (g, e), s in {(3, 3): "4/9", (3, 2): "5/9", (2, 3): "7/9", (2, 2): "2/9"}.items()
    },
    **{
        ("D2", 87, g, e): Fraction(s)
        for (g, e), s in {
            (2, 3): "7/10",
            (2, 2): "1/4",
            (2, 1): "1/20",
            (1, 2): "5/12",
            (1, 1): "5/12",
            (1, 0): "1/6",
        }.items()
    },
    **{
        ("D1", 87, g, e): Fraction(s)
        for (g, e), s in {(2, 2): "1/2", (2, 1): "1/2", (1, 2): "5/6", (1, 1): "1/6"}.items()
    },
}


def parse() -> tuple[dict[tuple[int, str], float], list[dict]]:
    """Split the table into fine-structure centres and parsed hyperfine transitions."""
    centers: dict[tuple[int, str], float] = {}
    for label, thz in Rb_transitions:
        if match := CENTER.match(label):
            centers[int(match["isotope"]), match["line"]] = thz
    rows = []
    for label, thz in Rb_transitions:
        if CENTER.match(label):
            continue
        match = LABEL.match(label)
        if match is None:
            raise ValueError(f"unrecognised Rb transition label {label!r}")
        isotope, line = int(match["isotope"]), match["line"]
        ground, excited = int(match["ground"]), int(match["excited"])
        rows.append(
            {
                "label": label,
                "line": line,
                "isotope": isotope,
                "f_ground": ground,
                "f_excited": excited,
                "thz": thz,
                "detuning_ghz": (thz - centers[isotope, line]) * 1e3,
                "strength": float(STRENGTH[line, isotope, ground, excited]),
            }
        )
    return centers, rows


def hyperfine_offsets(rows: list[dict], centers: dict[tuple[int, str], float]) -> dict:
    """Recover the level offsets (GHz from each manifold's centre of gravity).

    Every table entry is `centre + ground offset + excited offset`, so the
    offsets follow from the table by least squares once each manifold is pinned
    to its centre of gravity, sum((2F + 1) * offset) = 0 (Steck's convention).
    The residual is returned so a test can prove the table is of that form.
    """
    out: dict = {}
    for isotope in (85, 87):
        mine = [r for r in rows if r["isotope"] == isotope]
        grounds = sorted({r["f_ground"] for r in mine})
        excited = {
            line: sorted({r["f_excited"] for r in mine if r["line"] == line})
            for line in ("D1", "D2")
        }
        names = [("g", g) for g in grounds] + [
            (line, f) for line in ("D1", "D2") for f in excited[line]
        ]
        index = {name: i for i, name in enumerate(names)}
        equations, values = [], []
        for r in mine:
            row = np.zeros(len(names))
            row[index["g", r["f_ground"]]] = 1
            row[index[r["line"], r["f_excited"]]] = 1
            equations.append(row)
            values.append(r["detuning_ghz"])
        for key, levels in [("g", grounds), ("D1", excited["D1"]), ("D2", excited["D2"])]:
            row = np.zeros(len(names))
            for f in levels:
                row[index[key, f]] = 2 * f + 1
            equations.append(row)
            values.append(0.0)
        solution, *_ = np.linalg.lstsq(np.array(equations), np.array(values), rcond=None)
        residual = float(np.max(np.abs(np.array(equations) @ solution - np.array(values))))
        out[str(isotope)] = {
            "ground": {str(g): float(solution[index["g", g]]) for g in grounds},
            "D1": {str(f): float(solution[index["D1", f]]) for f in excited["D1"]},
            "D2": {str(f): float(solution[index["D2", f]]) for f in excited["D2"]},
            "residual_ghz": residual,
        }
    return out


def build() -> dict:
    centers, rows = parse()
    return {
        "generated_by": "scripts/export_rb_data.py",
        "source": "iyzee.devices.wavemeter.Rb_transitions (D. A. Steck, Rubidium D Line Data)",
        "centers_thz": {
            str(isotope): {line: centers[isotope, line] for line in ("D1", "D2")}
            for isotope in (85, 87)
        },
        "levels_ghz": hyperfine_offsets(rows, centers),
        "transitions": rows,
    }


def render(data: dict) -> str:
    """Deterministic text form, so the committed file is stable under re-export."""

    def clean(value):
        if isinstance(value, float):
            return round(value, 9)
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return json.dumps(clean(data), indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render(build()), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
