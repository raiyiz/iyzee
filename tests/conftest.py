import sys
import typing
from pathlib import Path

import pytest

# The project uses a src layout. Make the `iyzee` package importable (as
# `iyzee`, `iyzee.devices.mxa`, etc.) even when the package has not been pip-installed,
# so the test suite can run from a bare checkout without hardware.
src_dir = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(src_dir))

from iyzee.tui import logging_support  # noqa: E402 - see sys.path setup above


@pytest.fixture(autouse=True)
def _isolated_log_root(tmp_path: typing.Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect logging_support.LOG_ROOT to this test's own tmp_path.

    Nearly every test in this suite constructs its own ``IyzeeApp()``, and
    ``IyzeeApp.__init__`` installs the logging handler on construction
    (see ``logging_support.install``) — including its rotating file
    handler. Without this, every one of those tests would create and
    write to a real ``logs/iyzee.log`` inside the actual project checkout,
    the same kind of leak every test already avoids for saved-run data by
    redirecting ``experiment.io.DATA_ROOT`` to ``tmp_path``. This is the
    same idea, just automatic (autouse) instead of per-test, since unlike
    ``DATA_ROOT`` this isn't something most individual tests have any
    reason to know or care about.
    """
    monkeypatch.setattr(logging_support, "LOG_ROOT", tmp_path / "logs")


# -- real-hardware tests ------------------------------------------------------------------
#
# Everything under tests/hardware talks to real instruments. It is skipped unless
# ``--hardware`` is passed, and the subset that changes instrument state (marked
# ``actuates``) additionally needs ``--hardware-actuate``. The gating lives here, in the
# top-level conftest, so no hardware test can forget it: a test is "hardware" because of
# where it lives, not because its author remembered a marker.

HARDWARE_DIR = Path(__file__).parent / "hardware"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("hardware", "real-instrument tests")
    group.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="run the tests in tests/hardware against the real instruments "
        "(addresses come from IYZEE_*_IP / config.toml, as in the app)",
    )
    group.addoption(
        "--hardware-actuate",
        action="store_true",
        default=False,
        help="also run the hardware tests that change instrument state "
        "(open the shutter, run sweeps, move the laser). Requires --hardware.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--hardware-actuate") and not config.getoption("--hardware"):
        raise pytest.UsageError("--hardware-actuate requires --hardware")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_hardware = config.getoption("--hardware")
    allow_actuation = config.getoption("--hardware-actuate")
    for item in items:
        if HARDWARE_DIR in item.path.parents:
            item.add_marker(pytest.mark.hardware)
        if item.get_closest_marker("hardware") is None:
            continue
        # Skipped at collection time, so the instrument fixtures are never even set up.
        if not run_hardware:
            item.add_marker(pytest.mark.skip(reason="real-hardware test: pass --hardware to run"))
        elif item.get_closest_marker("actuates") and not allow_actuation:
            item.add_marker(
                pytest.mark.skip(
                    reason="changes instrument state: also pass --hardware-actuate to run"
                )
            )


def pytest_report_header(config: pytest.Config) -> list[str] | None:
    """Say exactly which machines a hardware run will talk to, before it does."""
    if not config.getoption("--hardware"):
        return None
    from iyzee.config import IP, ConfigError, address

    try:
        targets = ", ".join(f"{ip.name.lower()}={address(ip)}" for ip in IP)
    except ConfigError as exc:
        targets = f"unreadable config ({exc})"
    actuate = "ON" if config.getoption("--hardware-actuate") else "off"
    return [f"hardware: instruments {targets}", f"hardware: state-changing tests {actuate}"]
