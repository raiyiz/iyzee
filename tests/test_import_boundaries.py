"""Architectural boundaries that keep the terminal UI independent of GUI plotting backends."""

from __future__ import annotations

import subprocess
import sys


def test_importing_tui_does_not_initialize_matplotlib_pyplot() -> None:
    # Use a fresh interpreter so unrelated figure tests cannot pollute sys.modules.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import iyzee.tui.app; "
            "assert 'matplotlib.pyplot' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
