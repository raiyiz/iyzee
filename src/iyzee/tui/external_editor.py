"""Edit text in the user's own editor (``$VISUAL``, then ``$EDITOR``, then ``vi``), like ``git commit``.

This is how the console gets *real* Vim for anything bigger than a line: the prompt's text goes
to a temporary ``.py`` file, the editor runs on the real terminal, and the result comes back.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def edit_text(text: str, suffix: str = ".py") -> str | None:
    """The text after the editor closes, or ``None`` if it failed (e.g. Vim's ``:cq``)."""
    command = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as handle:
        handle.write(text)
    path = Path(handle.name)
    try:
        if subprocess.run([*command, str(path)], check=False).returncode != 0:  # noqa: S603
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")
    finally:
        path.unlink(missing_ok=True)
