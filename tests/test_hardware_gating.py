"""The real-hardware tests stay out of the way unless asked for.

The property that matters most is that a plain ``pytest`` never touches an instrument. These run
pytest in a subprocess against ``tests/hardware`` with every instrument address pointed at the
reserved TEST-NET range (RFC 5737), so that even a gating bug could not reach a real device.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNROUTABLE = "192.0.2.1"


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **{f"IYZEE_{name}_IP": UNROUTABLE for name in ("SCOPE", "NOISE_ANALYZER")},
        "IYZEE_POWER_SUPPLY_IP": UNROUTABLE,
        "IYZEE_WAVEMETER_IP": UNROUTABLE,
    }
    return subprocess.run(  # noqa: S603 - fixed argv and TEST-NET addresses isolate this test
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rs", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_by_default_every_hardware_test_is_skipped_and_nothing_is_connected() -> None:
    result = _pytest("tests/hardware")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pass --hardware to run" in result.stdout
    assert "passed" not in result.stdout and "failed" not in result.stdout
    assert "hardware: instruments" not in result.stdout  # the target list is for real runs


def test_state_changing_tests_need_their_own_flag() -> None:
    result = _pytest("tests/hardware", "--hardware", "-m", "actuates")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "also pass --hardware-actuate" in result.stdout
    assert "passed" not in result.stdout and "failed" not in result.stdout
    # The run says which machines it would have talked to.
    assert f"noise_analyzer={UNROUTABLE}" in result.stdout


def test_the_actuate_flag_alone_is_a_usage_error() -> None:
    result = _pytest("tests/hardware", "--hardware-actuate")
    assert result.returncode == 4
    assert "--hardware-actuate requires --hardware" in result.stdout + result.stderr


def test_anything_under_the_hardware_directory_is_marked_hardware() -> None:
    """Deselecting ``hardware`` leaves nothing in that directory (exit code 5: none selected)."""
    result = _pytest("tests/hardware", "--collect-only", "-q", "-m", "not hardware")
    assert result.returncode == 5, result.stdout + result.stderr
