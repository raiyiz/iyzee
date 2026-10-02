"""State the TUI hands from the sweep to the console.

Every device call in this codebase is blocking I/O and runs in a
``@work(thread=True)`` worker that reaches the UI only through
``self.app.call_from_thread(...)``; see ``Page._ui``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..experiment import StepResult


@dataclass(frozen=True)
class LastRun:
    """The most recently completed sweep, kept for the console to inspect.

    Set by ``SweepScreen._finish`` on ``self.app.last_run`` whenever a run
    collects at least one point, and read by ``ConsoleScreen`` to expose a
    ``results`` variable — so `np.mean(results[-1].traces["squeezing"])`
    works right after a sweep without leaving the TUI. ``path`` is where
    it was saved (``None`` if nothing was collected to save).
    """

    kind: str
    results: list[StepResult]
    path: Path | None
