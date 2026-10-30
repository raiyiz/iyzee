"""The notebooks work: each runs end to end on generated data. Rough by design.

The analysis they share lives in ``iyzee.workbench`` and is tested strictly in test_workbench.py.

Run with:
    uv run --group notebooks pytest tests/test_notebooks.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("polars")
pytest.importorskip("marimo")

ROOT = Path(__file__).parents[1]
NOTEBOOKS = ROOT / "notebooks"


@pytest.mark.parametrize(
    "notebook", ["workbench", "sweep_scope_analysis", "reproduce_frequency_sweep"]
)
def test_each_notebook_runs_every_cell_on_generated_data(notebook: str, demo: Path, tmp_path: Path):
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [sys.executable, str(NOTEBOOKS / f"{notebook}.py")],
        env={
            **os.environ,
            "IYZEE_DATA_DIR": str(demo),
            "IYZEE_WORKSETS_FILE": str(tmp_path / "worksets.json"),
        },
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
