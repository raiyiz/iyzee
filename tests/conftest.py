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
    """Close embedded IPython shells when each test finishes."""
    yield
    from iyzee.tui.ipython import IyzeeIPython

    for shell in list(IyzeeIPython._instances):
        shell.close()
