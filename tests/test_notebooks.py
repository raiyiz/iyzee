"""The notebooks work: each runs end to end on generated data, and the toolbox they share is sane.

Run with:
    uv run --group notebooks pytest tests/test_notebooks.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("marimo")

ROOT = Path(__file__).parents[1]
NOTEBOOKS = ROOT / "notebooks"

from iyzee.workbench.demo import write_demo  # noqa: E402


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    write_demo(root, now=datetime(2026, 10, 19, 12, tzinfo=UTC), seed=3)
    return root


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


