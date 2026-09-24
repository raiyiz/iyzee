import sys
from pathlib import Path

import pytest

# The project uses a src layout. Make the `iyzee` package importable (as
# `iyzee`, `iyzee.mxa`, etc.) even when the package has not been pip-installed,
# so the test suite can run from a bare checkout without hardware.
src_dir = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(src_dir))

from iyzee.tui import logging_support  # noqa: E402 - see sys.path setup above


@pytest.fixture(autouse=True)
def _isolated_log_root(tmp_path, monkeypatch):
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
