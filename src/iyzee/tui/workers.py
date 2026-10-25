"""State the TUI keeps from the last sweep.

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
    """The most recently completed sweep.

    Set by ``SweepScreen._finish`` on ``self.app.last_run`` whenever a run collects at
    least one point. ``path`` is where it was saved (``None`` if nothing was saved).
    """

    kind: str
    results: list[StepResult]
    path: Path | None
