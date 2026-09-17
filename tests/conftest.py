import sys
from pathlib import Path

import pytest

# The project uses a src layout. Make the `iyzee` package importable (as
# `iyzee`, `iyzee.mxa`, etc.) even when the package has not been pip-installed,
# so the test suite can run from a bare checkout without hardware.
src_dir = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(src_dir))


@pytest.fixture(autouse=True)
def close_embedded_ipython_shells():
    """Close every embedded IPython shell created by the current test.

    Embedded InteractiveShell instances register their own process-wide
    ``atexit`` callback. Tests intentionally create many independent shells,
    so leaving them alive until interpreter shutdown makes pytest hang while
    IPython performs SQLite history cleanup. Production history remains
    persistent; this fixture simply makes test-owned shells release it at
    test teardown instead of process teardown.
    """
    yield
    from iyzee.tui.ipython import IyzeeIPython

    for shell in list(IyzeeIPython._instances):
        shell.close()
